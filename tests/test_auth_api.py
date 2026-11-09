import os
import sys
from pathlib import Path
import json

# Add project root to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ["TESTING"] = "1"
os.environ["LANGSMITH_TRACING"] = "false"

import unittest
from unittest.mock import patch, MagicMock
from bson import ObjectId

from app import (
    auth_signup,
    auth_signin,
    auth_signout,
    auth_me,
    auth_update_profile,
    auth_avatar,
    get_avatar_file,
    conversations,
    history,
    pin_conversation,
)
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

            def sort(self, *args, **kwargs):
                return self

            def limit(self, n):
                return self.data[:n]

            def __iter__(self):
                return iter(self.data)

        return CursorMock(matched)


class MockRequest:
    def __init__(self, json_data=None, cookies=None, headers=None, query_params=None):
        self._json_data = json_data or {}
        self.cookies = cookies or {}
        self.headers = headers or {}
        self.query_params = query_params or {}

    async def json(self):
        return self._json_data


def run_coro(coro):
    """Run an async endpoint coroutine directly without network/socket binding."""
    try:
        task = coro
        val = None
        while True:
            try:
                sub = task.send(val)
                if hasattr(sub, "send"):
                    val = run_coro(sub)
                else:
                    val = None
            except StopIteration as se:
                return se.value
    finally:
        coro.close()


def parse_response(resp):
    """Extract (status_code, body_dict) from JSONResponse or raw dict."""
    if isinstance(resp, dict):
        return 200, resp
    status_code = getattr(resp, "status_code", 200)
    body = getattr(resp, "body", b"{}")
    if isinstance(body, bytes):
        data = json.loads(body.decode("utf-8"))
    else:
        data = json.loads(str(body))
    return status_code, data


class TestAuthAPIs(unittest.TestCase):
    def setUp(self):
        self.mock_collections = {
            "users": InMemoryCollection(),
            "sessions": InMemoryCollection(),
            "conversations": InMemoryCollection(),
            "chat_messages": InMemoryCollection(),
            "long_term_memory": InMemoryCollection(),
        }
        self.patcher1 = patch("database.get_collection", side_effect=lambda name: self.mock_collections[name])
        self.patcher2 = patch("mongo_client.get_collection", side_effect=lambda name: self.mock_collections[name])
        self.patcher1.start()
        self.patcher2.start()

    def tearDown(self):
        self.patcher1.stop()
        self.patcher2.stop()

    def test_signup_signin_signout_flow(self):
        # 1. Unauthenticated /auth/me
        req1 = MockRequest()
        res1 = run_coro(auth_me(req1))
        code1, data1 = parse_response(res1)
        self.assertEqual(code1, 200)
        self.assertFalse(data1["authenticated"])

        # 2. Signup
        signup_payload = {
            "username": "alice",
            "email": "alice@example.com",
            "password": "password123",
            "full_name": "Alice Wonderland",
        }
        req2 = MockRequest(json_data=signup_payload)
        res2 = run_coro(auth_signup(req2))
        code2, data2 = parse_response(res2)
        self.assertEqual(code2, 200)
        self.assertTrue(data2["success"])
        self.assertEqual(data2["user"]["username"], "alice")
        token = data2.get("token")
        self.assertTrue(token)

        # 3. Authenticated /auth/me using Bearer token header
        req3 = MockRequest(headers={"Authorization": f"Bearer {token}"})
        res3 = run_coro(auth_me(req3))
        code3, data3 = parse_response(res3)
        self.assertEqual(code3, 200)
        self.assertTrue(data3["authenticated"])
        self.assertEqual(data3["user"]["username"], "alice")

        # 4. Authenticated /auth/me using Cookie
        req4 = MockRequest(cookies={"tit_session": token})
        res4 = run_coro(auth_me(req4))
        code4, data4 = parse_response(res4)
        self.assertEqual(code4, 200)
        self.assertTrue(data4["authenticated"])
        self.assertEqual(data4["user"]["username"], "alice")

        # 5. Signout
        req5 = MockRequest(headers={"Authorization": f"Bearer {token}"})
        res5 = run_coro(auth_signout(req5))
        code5, data5 = parse_response(res5)
        self.assertEqual(code5, 200)
        self.assertTrue(data5["success"])

        # 6. Verify signed out
        req6 = MockRequest(headers={"Authorization": f"Bearer {token}"})
        res6 = run_coro(auth_me(req6))
        code6, data6 = parse_response(res6)
        self.assertFalse(data6["authenticated"])

        # 7. Signin with username
        req7 = MockRequest(json_data={"username": "alice", "password": "password123"})
        res7 = run_coro(auth_signin(req7))
        code7, data7 = parse_response(res7)
        self.assertEqual(code7, 200)
        self.assertTrue(data7["success"])

        # 7b. Signin with identifier field (as sent by frontend form)
        req7b = MockRequest(json_data={"identifier": "alice", "password": "password123"})
        res7b = run_coro(auth_signin(req7b))
        code7b, data7b = parse_response(res7b)
        self.assertEqual(code7b, 200)
        self.assertTrue(data7b["success"])

        # 8. Wrong password
        req8 = MockRequest(json_data={"username": "alice", "password": "wrongpassword"})
        res8 = run_coro(auth_signin(req8))
        code8, data8 = parse_response(res8)
        self.assertEqual(code8, 401)
        self.assertFalse(data8["success"])

    def test_conversations_user_isolation_endpoints(self):
        # Create user 1 (Alice)
        u1 = database.create_user("user_alice", "alice@test.com", "pass123456")
        s1 = database.create_session(u1["id"])

        # Create user 2 (Bob)
        u2 = database.create_user("user_bob", "bob@test.com", "pass123456")
        s2 = database.create_session(u2["id"])

        # Create conversation for Alice
        database.create_or_update_conversation("thread_alice_1", "Alice first chat", user_id=u1["id"])
        database.save_chat_message("thread_alice_1", "user", "Alice secret question", user_id=u1["id"])

        # Create conversation for Bob
        database.create_or_update_conversation("thread_bob_1", "Bob first chat", user_id=u2["id"])
        database.save_chat_message("thread_bob_1", "user", "Bob secret question", user_id=u2["id"])

        # Alice fetches conversations
        req_alice = MockRequest(headers={"Authorization": f"Bearer {s1}"})
        res_alice = run_coro(conversations(req_alice))
        code_a, data_a = parse_response(res_alice)
        self.assertEqual(code_a, 200)
        alice_convs = data_a["conversations"]
        self.assertEqual(len(alice_convs), 1)
        self.assertEqual(alice_convs[0]["thread_id"], "thread_alice_1")

        # Bob fetches conversations
        req_bob = MockRequest(headers={"Authorization": f"Bearer {s2}"})
        res_bob = run_coro(conversations(req_bob))
        code_b, data_b = parse_response(res_bob)
        self.assertEqual(code_b, 200)
        bob_convs = data_b["conversations"]
        self.assertEqual(len(bob_convs), 1)
        self.assertEqual(bob_convs[0]["thread_id"], "thread_bob_1")

        # Alice accesses her own history -> 200 OK
        req_hist_alice = MockRequest(headers={"Authorization": f"Bearer {s1}"})
        res_hist_alice = run_coro(history("thread_alice_1", req_hist_alice))
        code_ha, data_ha = parse_response(res_hist_alice)
        self.assertEqual(code_ha, 200)
        self.assertEqual(len(data_ha["messages"]), 1)
        self.assertEqual(data_ha["messages"][0]["content"], "Alice secret question")

        # Bob attempts to access Alice's conversation history -> 403 Forbidden!
        req_hist_bob = MockRequest(headers={"Authorization": f"Bearer {s2}"})
        res_hist_bob = run_coro(history("thread_alice_1", req_hist_bob))
        code_hb, data_hb = parse_response(res_hist_bob)
        self.assertEqual(code_hb, 403)
        self.assertIn("error", data_hb)

    def test_update_profile_and_avatar_endpoints(self):
        u = database.create_user("charlie", "charlie@example.com", "password123", "Charlie Brown")
        session_id = database.create_session(u["id"])

        # 1. Update profile without auth -> 401
        unauth_req = MockRequest(json_data={"full_name": "Charlie Updated"})
        unauth_res = run_coro(auth_update_profile(unauth_req))
        code_u, _ = parse_response(unauth_res)
        self.assertEqual(code_u, 401)

        # 2. Update profile with auth -> 200
        auth_req = MockRequest(
            headers={"Authorization": f"Bearer {session_id}"},
            json_data={"full_name": "Charlie The Great", "email": "charlie_new@example.com"}
        )
        auth_res = run_coro(auth_update_profile(auth_req))
        code, data = parse_response(auth_res)
        self.assertEqual(code, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["user"]["full_name"], "Charlie The Great")
        self.assertEqual(data["user"]["email"], "charlie_new@example.com")

        # 3. Avatar upload
        class FakeUploadFile:
            def __init__(self, filename, content):
                self.filename = filename
                self.content = content

            async def read(self):
                return self.content

        fake_avatar = FakeUploadFile("avatar.png", b"\x89PNG\r\n\x1a\nfakeimagebytes")
        avatar_req = MockRequest(headers={"Authorization": f"Bearer {session_id}"})
        avatar_res = run_coro(auth_avatar(avatar_req, file=fake_avatar))
        code_av, data_av = parse_response(avatar_res)
        self.assertEqual(code_av, 200)
        self.assertTrue(data_av["success"])
        self.assertIn("/uploads/avatars/", data_av["avatar_url"])
        self.assertEqual(data_av["user"]["avatar_url"], data_av["avatar_url"])

        # 4. Get avatar file
        filename = Path(data_av["avatar_url"]).name
        res_file = run_coro(get_avatar_file(filename))
        self.assertEqual(getattr(res_file, "status_code", 200), 200)

    def test_pin_conversation_endpoint(self):
        u_alice = database.create_user("alice_pin", "alice_pin@example.com", "pass123")
        u_bob = database.create_user("bob_pin", "bob_pin@example.com", "pass123")
        s_alice = database.create_session(u_alice["id"])
        s_bob = database.create_session(u_bob["id"])

        database.create_or_update_conversation("thread_alice_pin", "Alice Thread", user_id=u_alice["id"])

        # 1. Non-existent conversation -> 404
        req_non = MockRequest(headers={"Authorization": f"Bearer {s_alice}"})
        res_non = run_coro(pin_conversation("non_existent_thread_xyz", req_non))
        code_non, data_non = parse_response(res_non)
        self.assertEqual(code_non, 404)
        self.assertFalse(data_non["success"])

        # 2. Bob tries to pin Alice's thread -> 403
        req_bob = MockRequest(headers={"Authorization": f"Bearer {s_bob}"})
        res_bob = run_coro(pin_conversation("thread_alice_pin", req_bob))
        code_bob, data_bob = parse_response(res_bob)
        self.assertEqual(code_bob, 403)
        self.assertFalse(data_bob["success"])

        # 3. Alice pins her own thread -> 200, is_pinned=True
        req_alice = MockRequest(headers={"Authorization": f"Bearer {s_alice}"})
        res_alice = run_coro(pin_conversation("thread_alice_pin", req_alice))
        code_a, data_a = parse_response(res_alice)
        self.assertEqual(code_a, 200)
        self.assertTrue(data_a["success"])
        self.assertTrue(data_a["is_pinned"])

        # 4. Check GET /conversations returns is_pinned=True
        res_convs = run_coro(conversations(req_alice))
        code_c, data_c = parse_response(res_convs)
        self.assertEqual(code_c, 200)
        found_conv = next((c for c in data_c["conversations"] if c["thread_id"] == "thread_alice_pin"), None)
        self.assertIsNotNone(found_conv)
        self.assertTrue(found_conv["is_pinned"])

        # 5. Alice unpins her thread -> 200, is_pinned=False
        res_unpin = run_coro(pin_conversation("thread_alice_pin", req_alice))
        code_u, data_u = parse_response(res_unpin)
        self.assertEqual(code_u, 200)
        self.assertTrue(data_u["success"])
        self.assertFalse(data_u["is_pinned"])


if __name__ == "__main__":
    unittest.main()
