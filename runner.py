from kaggle_environments import make
from main import agent1,agent2


with open("decks/deck1.csv") as f:
    deck = [int(line) for line in f.readlines() if line.strip()]

env = make("cabt", configuration={"decks": [deck, deck]})
env.run([agent1, agent2])

for i, state in enumerate(env.state):
    print(f"Agent {i}: reward={state['reward']}, status={state['status']}")
    
html = env.render(mode="html")

with open("result.html", "w", encoding="utf-8") as f:
    f.write(html)
print("Simulation finished.")
