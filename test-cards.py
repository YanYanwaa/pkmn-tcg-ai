import sdk.api as api

cards = api.all_card_data()

print(f"Found {len(cards)} cards")

for card in cards:
    if (card.ex == True):
        print("EX:")
        print(card.cardId, card.name, card.cardType) 
    elif (card.megaEx == True):
        print("Mega EX:")
        print(card.cardId, card.name)
    elif (card.tera == True):
        print("Tera:")
        print(card.cardId, card.name)

# Card Types:
# 0 = Pokemon
# 1 = Item
# 2 = Tool
# 3 = Supporter
# 4 = Stadium
# 5 = Basic Energy
# 6 = Special Energy
