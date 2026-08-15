"""
Run repeated cabt matches between two agents and collect stats:
- win / loss / draw counts (win rate)
- KOs scored per agent (total and average per game)
- damage dealt per agent (total and average per game)

Usage:
    python run_experiment.py
"""

import csv
from collections import defaultdict
from dataclasses import dataclass, field

from kaggle_environments import make
from random_agent import random_agent
from main import agent as main_agent
from offence import offence_agent
from defence import defence_agent
from setup_agent import agent as setup_agent
import threat_detection as td
from sdk.api import AreaType, LogType, SelectContext, all_card_data, to_observation_class
from original import agent as boss_agent
# id -> name lookup, used to make the opening-pokemon report readable
CARD_NAMES = {c.cardId: c.name for c in all_card_data()}


# ---------------------------------------------------------------------------
# Stats tracking
# ---------------------------------------------------------------------------

@dataclass
class Tracker:
    """Cumulative + per-game stats, keyed by player index (0 or 1)."""
    games_played: int = 0
    wins: dict = field(default_factory=lambda: defaultdict(int))
    losses: dict = field(default_factory=lambda: defaultdict(int))
    draws: dict = field(default_factory=lambda: defaultdict(int))
    total_kos: dict = field(default_factory=lambda: defaultdict(int))
    total_damage: dict = field(default_factory=lambda: defaultdict(int))
    total_attacks: dict = field(default_factory=lambda: defaultdict(int))
    max_damage: dict = field(default_factory=lambda: defaultdict(int))
    max_damage_game: dict = field(default_factory=dict)  # which game each max occurred in

    # opening pokemon chosen this game, keyed by player index (0/1)
    _opening: dict = field(default_factory=dict)
    # {player_index: {card_name: {"games": n, "wins": n}}}
    opening_stats: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(lambda: {"games": 0, "wins": 0})))

    # live counters for the game currently in progress
    _kos: dict = field(default_factory=lambda: defaultdict(int))
    _damage: dict = field(default_factory=lambda: defaultdict(int))
    _attacks: dict = field(default_factory=lambda: defaultdict(int))

    rows: list = field(default_factory=list)

    def note_log(self, log):
        """Called by the wrapped agents for every log event they see."""
        if log.type == LogType.ATTACK:
            self._attacks[log.playerIndex] += 1

        elif log.type == LogType.HP_CHANGE:
            if log.value is not None and log.value < 0:
                # damage suffered by log.playerIndex's pokemon -> credited to the opponent
                dealer = 1 - log.playerIndex
                self._damage[dealer] += -log.value

        elif log.type == LogType.MOVE_CARD:
            if log.fromArea in (AreaType.ACTIVE, AreaType.BENCH) and log.toArea == AreaType.DISCARD:
                # a pokemon belonging to log.playerIndex left play into the discard -> KO'd
                scorer = 1 - log.playerIndex
                self._kos[scorer] += 1

    def note_opening(self, player_index, card_id):
        """Record the pokemon a player opens with, first call wins per game."""
        if player_index not in self._opening:
            self._opening[player_index] = card_id

    def start_game(self):
        self._kos = defaultdict(int)
        self._damage = defaultdict(int)
        self._attacks = defaultdict(int)
        self._opening = {}

    def end_game(self, game_no, names, rewards, statuses):
        row = {"game": game_no}
        for i, name in enumerate(names):
            reward = rewards[i]
            status = statuses[i]
            row[f"{name}_reward"] = reward
            row[f"{name}_status"] = status
            row[f"{name}_kos"] = self._kos[i]
            row[f"{name}_damage"] = self._damage[i]
            row[f"{name}_attacks"] = self._attacks[i]

            self.total_kos[i] += self._kos[i]
            self.total_damage[i] += self._damage[i]
            self.total_attacks[i] += self._attacks[i]
            if self._damage[i] > self.max_damage[i]:
                self.max_damage[i] = self._damage[i]
                self.max_damage_game[i] = game_no

            if reward == 1:
                self.wins[i] += 1
            elif reward == -1:
                self.losses[i] += 1
            else:
                self.draws[i] += 1

            opening_id = self._opening.get(i)
            opening_name = CARD_NAMES.get(opening_id, f"unknown({opening_id})") if opening_id is not None else "none"
            row[f"{name}_opening"] = opening_name

            stat = self.opening_stats[i][opening_name]
            stat["games"] += 1
            if reward == 1:
                stat["wins"] += 1

        self.rows.append(row)
        self.games_played += 1

    def print_summary(self, names):
        print("\n=== Summary over", self.games_played, "games ===")
        for i, name in enumerate(names):
            g = self.games_played or 1
            print(f"{name} (player {i}):")
            print(f"  Win rate      : {self.wins[i] / g:.1%}  ({self.wins[i]}W / {self.losses[i]}L / {self.draws[i]}D)")
            print(f"  Avg KOs/game  : {self.total_kos[i] / g:.2f}")
            print(f"  Avg damage/game: {self.total_damage[i] / g:.1f}")
            print(f"  Max damage/game: {self.max_damage[i]}  (game {self.max_damage_game.get(i, '-')})")
            print(f"  Avg attacks/game: {self.total_attacks[i] / g:.2f}")

            print("  Win rate by opening pokemon:")
            for opening_name, s in sorted(self.opening_stats[i].items(), key=lambda kv: -kv[1]["games"]):
                win_rate = s["wins"] / s["games"] if s["games"] else 0
                print(f"    {opening_name:<20} {win_rate:.1%}  ({s['wins']}W / {s['games']} games)")

    def to_csv(self, path):
        if not self.rows:
            return
        with open(path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=list(self.rows[0].keys()))
            writer.writeheader()
            writer.writerows(self.rows)


def make_tracking_agent(raw_agent, tracker: Tracker):
    """Wrap an agent so every log it observes is fed to the tracker, and its
    SETUP_ACTIVE_POKEMON pick is recorded as its game-opening pokemon."""
    def wrapped(obs_dict):
        obs = to_observation_class(obs_dict)
        if obs.current is not None:
            for log in obs.logs:
                tracker.note_log(log)

        action = raw_agent(obs_dict)

        if (obs.select is not None
                and obs.select.context == SelectContext.SETUP_ACTIVE_POKEMON
                and obs.current is not None
                and action):
            my_index = obs.current.yourIndex
            chosen_option = obs.select.option[action[0]]
            # the setup pick comes from the player's own hand
            if chosen_option.area == AreaType.HAND and chosen_option.playerIndex == my_index:
                hand = obs.current.players[my_index].hand
                if hand is not None:
                    card = hand[chosen_option.index]
                    tracker.note_opening(my_index, card.id)

        return action
    return wrapped



# ---------------------------------------------------------------------------
# Run experiment
# ---------------------------------------------------------------------------

def main():
    with open("deck.csv") as f:
        deck1 = [int(line) for line in f.readlines() if line.strip()]
    with open("lucario.csv") as f:
        deck2 = [int(line) for line in f.readlines() if line.strip()]
    NUM_RUNS = 500
    NAMES = ["main_agent", "boss_agent"]

    tracker = Tracker()

    # Wrap once; the wrapped closures read tracker's live counters each call,
    # which we reset via tracker.start_game() before every episode.
    agent_a = make_tracking_agent(main_agent, tracker)
    agent_b = make_tracking_agent(boss_agent, tracker)

    for run_no in range(1, NUM_RUNS + 1):
        # Fresh env per game avoids relying on cabt's internal reset behaviour.
        env = make("cabt", configuration={"decks": [deck1, deck1]})

        tracker.start_game()
        env.run([agent_a, agent_b])

        rewards = [s["reward"] for s in env.state]
        statuses = [s["status"] for s in env.state]
        tracker.end_game(run_no, NAMES, rewards, statuses)

        print(f"Game {run_no}: rewards={rewards}, statuses={statuses}, "
              f"kos={dict(tracker._kos)}, damage={dict(tracker._damage)}")

    tracker.print_summary(NAMES)
    tracker.to_csv("tests/experiment_results.csv")
    print("\nPer-game results written to experiment_results.csv")


if __name__ == "__main__":
    main()