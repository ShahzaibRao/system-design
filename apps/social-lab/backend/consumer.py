"""Kafka consumer for async likes (Chapter 24).

Reads like events from the post-likes topic and applies them to MongoDB
with the same check-then-push rule as the sync path, so duplicate events
are harmless (reprocessing after a crash is safe). After applying, it
invalidates the feed cache so the UI catches up.
"""
import json
import os

import redis
from bson import ObjectId
from kafka import KafkaConsumer
from pymongo import MongoClient

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
REDIS_HOST = os.environ.get("REDIS_HOST", "localhost")
REDIS_PORT = int(os.environ.get("REDIS_PORT", "6379"))

TOPIC = "post-likes"
GROUP_ID = "likes-group"


def invalidate_feed_cache(r):
    for key in r.scan_iter("feed:*"):
        r.delete(key)


def main():
    client = MongoClient(MONGO_URI)
    posts = client["social_lab"]["posts"]
    r = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)
    consumer = KafkaConsumer(
        TOPIC,
        bootstrap_servers=KAFKA_BOOTSTRAP_SERVERS,
        group_id=GROUP_ID,
        value_deserializer=lambda v: json.loads(v.decode("utf-8")),
        auto_offset_reset="earliest",
        enable_auto_commit=True,
    )
    print("likes consumer started, waiting for events...", flush=True)
    for msg in consumer:
        event = msg.value
        post_id = event.get("post_id")
        username = event.get("username")
        try:
            post = posts.find_one({"_id": ObjectId(post_id)})
        except Exception as exc:
            print("skip bad event %r: %s" % (event, exc), flush=True)
            continue
        if post is None:
            print("skip like for missing post %s" % post_id, flush=True)
            continue
        if username in post.get("liked_by", []):
            print("skip duplicate like %s on %s" % (username, post_id), flush=True)
            continue
        posts.update_one({"_id": post["_id"]}, {"$push": {"liked_by": username}})
        invalidate_feed_cache(r)
        print("applied like %s on %s" % (username, post_id), flush=True)


if __name__ == "__main__":
    main()
