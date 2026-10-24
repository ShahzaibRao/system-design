import hashlib
import math


class BloomFilter:
    """Simple Bloom filter, no new dependency.
    capacity: kitne usernames rakhne ki umeed hai
    error_rate: false positive ki umeed, jaise 0.01 matlab 1%
    """

    def __init__(self, capacity=10000, error_rate=0.01):
        self.capacity = capacity
        self.error_rate = error_rate
        # Size formula: jitne zyada items, utni zyada bits chahiye
        self.size = int(-(capacity * math.log(error_rate)) / (math.log(2) ** 2))
        # Hash functions ki tadaad, size aur capacity se nikalti hai
        self.hash_count = max(1, int((self.size / capacity) * math.log(2)))
        self.bits = [False] * self.size

    def _hashes(self, item):
        # Double hashing: md5 aur sha256 se 2 hash, phir un se k hash banate hain
        h1 = int(hashlib.md5(item.encode("utf-8")).hexdigest(), 16)
        h2 = int(hashlib.sha256(item.encode("utf-8")).hexdigest(), 16)
        for i in range(self.hash_count):
            yield (h1 + i * h2) % self.size

    def add(self, item):
        for index in self._hashes(item):
            self.bits[index] = True

    def might_contain(self, item):
        # Koi ek bit off hai to yaqeeni nahi hai, sab on hain to shayad hai
        return all(self.bits[index] for index in self._hashes(item))

    def __contains__(self, item):
        return self.might_contain(item)
