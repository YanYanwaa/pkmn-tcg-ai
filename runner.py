from kaggle_environments import make
from main import agent

with open("decks/deck1.csv") as f:
    deck = [int(line) for line in f.readlines() if line.strip()]

env = make("cabt", configuration={"decks": [deck, deck]})
env.run([agent, agent])

html = env.render(mode="html")

with open("result.html", "w", encoding="utf-8") as f:
    f.write(html)
print("Simulation finished.")