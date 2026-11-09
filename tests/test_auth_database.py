import unittest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock
from bson import ObjectId

import database


class InMemoryCollection:
    def __init__(self):
        self.docs = []

    def insert_one(self, doc):
        d = dict(doc)
        if "_id" not in d:
            d["_id"] = ObjectId()
        self.docs.append(d)
        res = MagicMock()
        res.inserted_id = d["_id"]
        return res

    def find_one(self, query, projection=None):
        for doc in self.docs:
            match = True
            for k, v in query.items():
                if k == "$or":
                    or_match = False
                    for sub_q in v:
                        if all(doc.get(sk) == sv for sk, sv in sub_q.items()):
                            or_match = True
                            break
                    if not or_match:
                        match = False
                        break
                elif doc.get(k) != v:
                    match = False
                    break
            if match:
                res = dict(doc)
                if projection and "_id" in projection and projection["_id"] == 0:
                    res.pop("_id", None)
                return res
        return None

    def update_one(self, filter_q, update_q, upsert=False):
        found = None
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in filter_q.items()):
                found = doc
                break
        if not found and upsert:
            new_doc = {}
            if "$setOnInsert" in update_q:
                new_doc.update(update_q["$setOnInsert"])
            if "$set" in update_q:
                new_doc.update(update_q["$set"])
            if "_id" not in new_doc:
                new_doc["_id"] = ObjectId()
            self.docs.append(new_doc)
            return MagicMock(upserted_id=new_doc["_id"])
        elif found:
            if "$set" in update_q:
                found.update(update_q["$set"])
            return MagicMock(modified_count=1)
        return MagicMock(modified_count=0)

    def delete_one(self, query):
        for idx, doc in enumerate(self.docs):
            if all(doc.get(k) == v for k, v in query.items()):
                self.docs.pop(idx)
                return MagicMock(deleted_count=1)
        return MagicMock(deleted_count=0)

    def delete_many(self, query):
        initial = len(self.docs)
        self.docs = [d for d in self.docs if not all(d.get(k) == v for k, v in query.items())]
        return MagicMock(deleted_count=initial - len(self.docs))

    def find(self, query=None, projection=None):
        query = query or {}
        matched = []
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                res = dict(doc)
                if projection and "_id" in projection and projection["_id"] == 0:
                    res.pop("_id", None)
                matched.append(res)

        class CursorMock:
            def __init__(self, data):
                self.data = data

            def sort(self, field, order):
                return self

            def limit(self, n):
                return self.data[:n]

            def __iter__(self):
                return iter(self.data)

        return CursorMock(matched)


class TestAuthDatabase(unittest.TestCase):
    def setUp(self):
        self.mock_collections = {
            "users": InMemoryCollection(),
            "sessions": InMemoryCollection(),
            "conversations": InMemoryCollection(),
            "chat_messages": InMemoryCollection(),
            "long_term_memory": InMemoryCollection(),
        }
        self.patcher = patch("database.get_collection", side_effect=lambda name: self.mock_collections[name])
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_password_hashing_and_verification(self):
        hashed = database.hash_password("mySecurePassword123")
        self.assertNotEqual(hashed, "mySecurePassword123")
        self.assertTrue(database.verify_password("mySecurePassword123", hashed))
        self.assertFalse(database.verify_password("wrongPassword", hashed))

    def test_create_user_success_and_duplicate_validation(self):
        user = database.create_user("son_nguyen", "son@example.com", "mypassword123", "Nguyen Thai Son")
        self.assertEqual(user["username"], "son_nguyen")
        self.assertEqual(user["email"], "son@example.com")
        self.assertEqual(user["full_name"], "Nguyen Thai Son")
        self.assertNotIn("hashed_password", user)

        # Duplicate username should raise ValueError
        with self.assertRaises(ValueError):
            database.create_user("son_nguyen", "other@example.com", "pass123456")

        # Duplicate email should raise ValueError
        with self.assertRaises(ValueError):
            database.create_user("other_user", "son@example.com", "pass123456")

    def test_create_user_validation_rules(self):
        with self.assertRaises(ValueError):
            database.create_user("ab", "valid@email.com", "123456")  # username too short
        with self.assertRaises(ValueError):
            database.create_user("valid_user", "invalid-email", "123456")  # bad email
        with self.assertRaises(ValueError):
            database.create_user("valid_user", "valid@email.com", "123")  # password too short

    def test_authenticate_user(self):
        database.create_user("thai_son", "thaison@gmail.com", "secretpass999")

        # Authenticate by username
        user_by_uname = database.authenticate_user("thai_son", "secretpass999")
        self.assertIsNotNone(user_by_uname)
        self.assertEqual(user_by_uname["username"], "thai_son")

        # Authenticate by email
        user_by_email = database.authenticate_user("thaison@gmail.com", "secretpass999")
        self.assertIsNotNone(user_by_email)
        self.assertEqual(user_by_email["email"], "thaison@gmail.com")

        # Wrong password
        self.assertIsNone(database.authenticate_user("thai_son", "wrongpass"))

        # Non-existent user
        self.assertIsNone(database.authenticate_user("ghost_user", "somepass"))

    def test_session_lifecycle(self):
        session_id = database.create_session("user_123", duration_days=2)
        self.assertTrue(len(session_id) > 20)

        # Retrieve valid session
        session = database.get_session(session_id)
        self.assertIsNotNone(session)
        self.assertEqual(session["user_id"], "user_123")

        # Delete session (logout)
        deleted = database.delete_session(session_id)
        self.assertTrue(deleted)
        self.assertIsNone(database.get_session(session_id))

    def test_conversation_user_isolation(self):
        # Create conversation for user_A
        database.create_or_update_conversation("thread_A1", "Hello from A1", user_id="user_A")
        database.create_or_update_conversation("thread_A2", "Hello from A2", user_id="user_A")
        # Create conversation for user_B
        database.create_or_update_conversation("thread_B1", "Hello from B1", user_id="user_B")

        # List conversations for user_A
        convs_A = database.list_conversations(user_id="user_A")
        self.assertEqual(len(convs_A), 2)
        thread_ids_A = {c.thread_id for c in convs_A}
        self.assertIn("thread_A1", thread_ids_A)
        self.assertIn("thread_A2", thread_ids_A)
        self.assertNotIn("thread_B1", thread_ids_A)

        # List conversations for user_B
        convs_B = database.list_conversations(user_id="user_B")
        self.assertEqual(len(convs_B), 1)
        self.assertEqual(convs_B[0].thread_id, "thread_B1")


if __name__ == "__main__":
    unittest.main()
