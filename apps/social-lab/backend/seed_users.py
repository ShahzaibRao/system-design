import bcrypt
from pymongo import MongoClient

client = MongoClient("mongodb://localhost:27017")
db = client["social_lab"]
users = db["users"]

users.delete_many({"username": {"$regex": "^seed"}})
hashed = bcrypt.hashpw(b"12345", bcrypt.gensalt()).decode("utf-8")
docs = [
    {"username": f"seed{i:06d}", "email": f"seed{i:06d}@test.com", "password": hashed}
    for i in range(10000)
]
for i in range(0, len(docs), 1000):
    users.insert_many(docs[i:i + 1000])

print("seeded:", users.count_documents({"username": {"$regex": "^seed"}}))
print("total:", users.count_documents({}))
