"""Project invitation lifecycle, API delivery, and cross-worker notification checks."""

import asyncio
import copy
import json
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from storage import projects
from services import email_service, project_events


def matches(row, query):
    for key, wanted in query.items():
        if key == "$or":
            if not any(matches(row, clause) for clause in wanted):
                return False
            continue
        actual = row.get(key)
        if isinstance(wanted, dict):
            for op, value in wanted.items():
                if op == "$exists" and (key in row) != value:
                    return False
                if op == "$ne" and actual == value:
                    return False
                if op == "$in" and actual not in value:
                    return False
                if op == "$gt":
                    if actual is None:
                        return False
                    if isinstance(actual, datetime) and actual.tzinfo is None:
                        actual = actual.replace(tzinfo=timezone.utc)
                    if actual <= value:
                        return False
        elif actual != wanted:
            return False
    return True


class Cursor(list):
    def sort(self, key, direction=1):
        return Cursor(sorted(self, key=lambda row: row.get(key, ""), reverse=direction < 0))


class Collection:
    """Small Mongo test double; preserves unique project/user membership pairs."""

    def __init__(self):
        self.rows = []

    def insert_one(self, row):
        row = copy.deepcopy(row)
        row.setdefault("_id", ObjectId())
        if "project_id" in row and "user_id" in row and any(
            r.get("project_id") == row["project_id"] and r.get("user_id") == row["user_id"] for r in self.rows
        ):
            raise DuplicateKeyError("duplicate membership")
        self.rows.append(row)
        return SimpleNamespace(inserted_id=row["_id"])

    def find(self, query, projection=None):
        rows = [copy.deepcopy(row) for row in self.rows if matches(row, query)]
        if projection:
            rows = [{key: value for key, value in row.items() if key == "_id" or projection.get(key)} for row in rows]
        return Cursor(rows)

    def find_one(self, query, projection=None):
        return next(iter(self.find(query, projection)), None)

    def update_one(self, query, update):
        for row in self.rows:
            if matches(row, query):
                before = copy.deepcopy(row)
                row.update(copy.deepcopy(update.get("$set", {})))
                for key in update.get("$unset", {}):
                    row.pop(key, None)
                return SimpleNamespace(matched_count=1, modified_count=int(row != before))
        return SimpleNamespace(matched_count=0, modified_count=0)

    def find_one_and_update(self, query, update, upsert=False, **kwargs):
        row = self.find_one(query)
        if row:
            self.update_one({"_id": row["_id"]}, update)
            return self.find_one({"_id": row["_id"]})
        if upsert:
            row = {**update.get("$setOnInsert", {}), **update.get("$set", {})}
            inserted = self.insert_one(row)
            return self.find_one({"_id": inserted.inserted_id})
        return None

    def delete_one(self, query):
        row = self.find_one(query)
        if row:
            self.rows = [r for r in self.rows if r["_id"] != row["_id"]]
        return SimpleNamespace(deleted_count=int(row is not None))


class ProjectInvitationTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 11, 26, 8, tzinfo=timezone.utc)
        self.owner, self.recipient, self.stranger, self.project = [ObjectId() for _ in range(4)]
        self.db = {name: Collection() for name in ("projects", "users", "project_members")}
        self.db["projects"].insert_one({"_id": self.project, "owner_id": self.owner, "name": "Project <One>", "updated_at": self.now})
        for oid, name in [(self.owner, "owner"), (self.recipient, "recipient"), (self.stranger, "stranger")]:
            self.db["users"].insert_one({"_id": oid, "username": name, "email": f"{name}@example.com", "full_name": name})
        self.addCleanup(patch.stopall)
        patch.object(projects, "get_collection", side_effect=self.db.__getitem__).start()
        patch.object(projects, "_now", side_effect=lambda: self.now).start()

    def invite(self, role="chat_only", days=7):
        return projects.invite_project_member(str(self.project), str(self.owner), " RECIPIENT@example.com ", role, days)

    def test_token_is_hashed_and_hidden_from_member_and_pending_lists(self):
        invite = self.invite(days=3)
        row = self.db["project_members"].rows[0]
        self.assertEqual(row["expires_at"], self.now + timedelta(days=3))
        self.assertEqual(row["invitation_token_hash"], projects._token_hash(invite["invitation_token"]))
        self.assertNotIn(invite["invitation_token"], json.dumps(row, default=str))
        for public in [invite, *projects.list_project_members(str(self.project)), *projects.list_pending_invitations(str(self.recipient))]:
            self.assertNotIn("invitation_token_hash", public)
        pending = projects.list_pending_invitations(str(self.recipient))[0]
        self.assertEqual(set(pending["project"]), {"_id", "name"})

    def test_invited_user_has_no_access_then_accepts_once(self):
        invite = self.invite()
        self.assertIsNone(projects.get_membership(str(self.project), str(self.recipient)))
        token = invite["invitation_token"]
        self.assertIsNone(projects.preview_project_invitation(token, str(self.stranger)))
        self.assertIsNone(projects.respond_to_invitation_token(token, str(self.stranger), True))
        self.assertEqual(projects.respond_to_invitation_token(token, str(self.recipient), True), str(self.project))
        self.assertEqual(projects.get_membership(str(self.project), str(self.recipient))["role"], "chat_only")
        self.assertIsNone(projects.respond_to_invitation_token(token, str(self.recipient), True))
        self.assertNotIn("invitation_token_hash", self.db["project_members"].rows[0])

    def test_expiry_blocks_link_and_in_app_accept_and_decline(self):
        invite = self.invite(days=1)
        self.now += timedelta(days=1)
        with self.assertRaisesRegex(ValueError, "expired"):
            projects.preview_project_invitation(invite["invitation_token"], str(self.recipient))
        self.assertFalse(projects.respond_to_invitation(invite["_id"], str(self.recipient), True))
        self.assertFalse(projects.respond_to_invitation(invite["_id"], str(self.recipient), False))
        self.assertEqual(projects.list_pending_invitations(str(self.recipient)), [])
        self.assertEqual(projects.list_project_members(str(self.project))[1]["status"], "expired")

    def test_legacy_invitation_expires_and_handles_naive_mongo_dates(self):
        invite = self.invite()
        row = self.db["project_members"].rows[0]
        row.pop("expires_at")
        row["created_at"] = self.now.replace(tzinfo=None) - timedelta(days=6)
        self.assertEqual(len(projects.list_pending_invitations(str(self.recipient))), 1)
        self.now += timedelta(days=1)
        self.assertFalse(projects.respond_to_invitation(invite["_id"], str(self.recipient), True))

    def test_reinvite_rotates_link_reuses_row_and_updates_role_and_deadline(self):
        old = self.invite()
        self.assertTrue(projects.respond_to_invitation(old["_id"], str(self.recipient), False))
        new = self.invite("editor", 14)
        self.assertEqual(old["_id"], new["_id"])
        self.assertNotEqual(old["invitation_token"], new["invitation_token"])
        self.assertIsNone(projects.preview_project_invitation(old["invitation_token"], str(self.recipient)))
        self.assertEqual(projects.preview_project_invitation(new["invitation_token"], str(self.recipient))["role"], "editor")
        self.assertEqual(len(self.db["project_members"].rows), 1)
        self.assertIsNone(self.db["project_members"].rows[0]["accepted_at"])

    def test_pending_and_expired_invitations_can_be_renewed(self):
        old = self.invite(days=1)
        pending = self.invite("editor", 3)
        self.assertIsNone(projects.preview_project_invitation(old["invitation_token"], str(self.recipient)))
        self.now += timedelta(days=4)
        renewed = self.invite(days=7)
        self.assertEqual(pending["_id"], renewed["_id"])
        self.assertEqual(renewed["status"], "invited")

    def test_revocation_removes_access_and_invalidates_links(self):
        invite = self.invite()
        self.assertFalse(projects.remove_project_member(str(self.project), str(self.stranger), str(self.recipient)))
        self.assertTrue(projects.remove_project_member(str(self.project), str(self.owner), str(self.recipient)))
        self.assertIsNone(projects.preview_project_invitation(invite["invitation_token"], str(self.recipient)))
        invite = self.invite()
        projects.respond_to_invitation_token(invite["invitation_token"], str(self.recipient), True)
        projects.remove_project_member(str(self.project), str(self.owner), str(self.recipient))
        self.assertIsNone(projects.get_membership(str(self.project), str(self.recipient)))

    def test_accepted_member_does_not_expire_and_only_owner_changes_roles(self):
        invite = self.invite(days=1)
        projects.respond_to_invitation(invite["_id"], str(self.recipient), True)
        self.now += timedelta(days=30)
        self.assertIsNotNone(projects.get_membership(str(self.project), str(self.recipient)))
        self.assertFalse(projects.update_member_role(str(self.project), str(self.stranger), str(self.recipient), "editor"))
        self.assertTrue(projects.update_member_role(str(self.project), str(self.owner), str(self.recipient), "editor"))
        self.assertTrue(projects.can_edit_project(str(self.project), str(self.recipient)))
        with self.assertRaisesRegex(ValueError, "already"):
            self.invite()

    def test_owner_and_input_validation(self):
        with self.assertRaises(PermissionError):
            projects.invite_project_member(str(self.project), str(self.stranger), "recipient", "editor")
        for duration in [0, 2, 31, True, "7", None]:
            with self.subTest(duration=duration), self.assertRaises(ValueError):
                self.invite(days=duration)
        for role in ["owner", "viewer", []]:
            with self.subTest(role=role), self.assertRaises(ValueError):
                self.invite(role=role)

    def test_concurrent_accept_cannot_be_overwritten_by_reinvite(self):
        self.invite()
        members = self.db["project_members"]
        with patch.object(members, "find_one_and_update", side_effect=DuplicateKeyError("accepted during invite")):
            with self.assertRaisesRegex(ValueError, "already"):
                self.invite("editor")

    def test_deleted_project_cannot_grant_access_or_accept_invitation(self):
        invite = self.invite()
        self.db["projects"].delete_one({"_id": self.project})
        self.assertFalse(projects.respond_to_invitation(invite["_id"], str(self.recipient), True))
        self.assertIsNone(projects.preview_project_invitation(invite["invitation_token"], str(self.recipient)))
        self.db["project_members"].rows[0]["status"] = "accepted"
        self.assertIsNone(projects.get_membership(str(self.project), str(self.recipient)))

    def test_realtime_revision_tracks_expiry_roles_acceptance_and_revocation(self):
        before = projects.project_update_state(str(self.recipient))
        invite = self.invite(days=1)
        pending = projects.project_update_state(str(self.recipient))
        self.assertNotEqual(before, pending)
        self.assertEqual(pending, projects.project_update_state(str(self.recipient)))
        self.now += timedelta(days=1)
        self.assertNotEqual(pending, projects.project_update_state(str(self.recipient)))
        invite = self.invite()
        projects.respond_to_invitation(invite["_id"], str(self.recipient), True)
        accepted = projects.project_update_state(str(self.owner))
        projects.update_member_role(str(self.project), str(self.owner), str(self.recipient), "editor")
        self.assertNotEqual(accepted["members_revision"], projects.project_update_state(str(self.owner))["members_revision"])
        projects.remove_project_member(str(self.project), str(self.owner), str(self.recipient))
        self.assertNotEqual(accepted, projects.project_update_state(str(self.owner)))


class ProjectEmailTests(unittest.TestCase):
    def test_html_escapes_names_and_contains_link_role_and_expiry(self):
        with patch.object(email_service, "_send_message") as deliver:
            email_service.send_project_invitation("to@example.com", '<img src=x>', '<script>x</script>', "editor", "https://chat.example/#project-invite=abc", "2026-12-03T08:00:00+00:00")
        message = deliver.call_args.args[0]
        self.assertEqual(message["To"], "to@example.com")
        html = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("&lt;img src=x&gt;", html)
        self.assertNotIn("<script>", html)
        self.assertIn("https://chat.example/#project-invite=abc", html)
        self.assertIn("03/12/2026", html)
        self.assertIn("Có thể chỉnh sửa", html)

    def test_verification_email_still_uses_shared_delivery(self):
        with patch.object(email_service, "_send_message") as deliver:
            email_service.send_verification_code("to@example.com", "123456")
        self.assertIn("123456", deliver.call_args.args[0].get_body(preferencelist=("plain",)).get_content())


class ProjectAPITests(unittest.TestCase):
    def setUp(self):
        from api import projects as api
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        self.api = api
        self.fixture = ProjectInvitationTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.user = patch.object(api, "get_current_user", return_value={"id": str(self.fixture.owner), "full_name": "Owner"}).start()
        self.mail = patch.object(api, "send_project_invitation").start()
        patch.dict(os.environ, {"PUBLIC_APP_URL": "https://chat.example"}).start()
        app = FastAPI()
        app.include_router(api.router, prefix="/api")
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def create(self, **kwargs):
        return self.client.post(f"/api/projects/{self.fixture.project}/members", json={"identifier": "recipient", "role": "editor", **kwargs})

    def test_invitation_email_link_and_recipient_response_end_to_end(self):
        response = self.create(expiry_days=3)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertTrue(body["email_sent"])
        self.assertTrue(body["invitation_url"].startswith("https://chat.example/#project-invite="))
        self.assertNotIn("invitation_token", body["invitation"])
        token = body["invitation_url"].split("=", 1)[1]
        self.mail.assert_called_once()
        self.assertEqual(self.mail.call_args.args[0], "recipient@example.com")
        self.assertEqual(self.mail.call_args.args[4], body["invitation_url"])
        self.user.return_value = {"id": str(self.fixture.recipient)}
        preview = self.client.post("/api/projects/invitations/preview", json={"token": token})
        self.assertEqual(preview.status_code, 200)
        self.assertEqual(preview.json()["invitation"]["project_name"], "Project <One>")
        accepted = self.client.post("/api/projects/invitations/respond", json={"token": token, "accept": True})
        self.assertEqual(accepted.json()["project_id"], str(self.fixture.project))
        self.assertEqual(self.client.post("/api/projects/invitations/respond", json={"token": token, "accept": True}).status_code, 404)

    def test_delivery_failure_keeps_invitation_and_copyable_link(self):
        self.mail.side_effect = RuntimeError("SMTP offline")
        with patch.object(self.api.logger, "warning"):
            response = self.create()
        self.assertEqual(response.status_code, 201)
        self.assertFalse(response.json()["email_sent"])
        self.assertTrue(response.json()["invitation_url"])
        self.assertEqual(len(self.fixture.db["project_members"].rows), 1)

    def test_authentication_and_owner_permissions(self):
        self.user.return_value = None
        for path in ["/api/projects/events", "/api/projects/invitations"]:
            self.assertEqual(self.client.get(path).status_code, 401)
        self.assertEqual(self.client.post("/api/projects/invitations/preview", json={"token": "x"}).status_code, 401)
        self.assertEqual(self.create().status_code, 401)
        self.user.return_value = {"id": str(self.fixture.stranger)}
        self.assertEqual(self.create().status_code, 403)
        self.mail.assert_not_called()

    def test_expired_link_wrong_account_and_invalid_json(self):
        response = self.create(expiry_days=1).json()
        token = response["invitation_url"].split("=", 1)[1]
        self.user.return_value = {"id": str(self.fixture.stranger)}
        self.assertEqual(self.client.post("/api/projects/invitations/preview", json={"token": token}).status_code, 404)
        self.user.return_value = {"id": str(self.fixture.recipient)}
        self.fixture.now += timedelta(days=1)
        self.assertEqual(self.client.post("/api/projects/invitations/preview", json={"token": token}).status_code, 410)
        self.assertEqual(self.client.post("/api/projects/invitations/respond", json={"token": token, "accept": True}).status_code, 410)
        for path in ["preview", "respond"]:
            self.assertEqual(self.client.post(f"/api/projects/invitations/{path}", content="{bad", headers={"Content-Type": "application/json"}).status_code, 400)
        self.assertEqual(self.client.post("/api/projects/invitations/respond", json={"token": token, "accept": "false"}).status_code, 400)

    def test_invalid_duration_or_url_does_not_create_or_email(self):
        self.assertEqual(self.create(expiry_days=500).status_code, 400)
        with patch.dict(os.environ, {"PUBLIC_APP_URL": "javascript:bad"}):
            self.assertEqual(self.create().status_code, 400)
        self.assertEqual(self.fixture.db["project_members"].rows, [])
        self.mail.assert_not_called()

    def test_events_route_is_registered_before_project_detail_and_disables_buffering(self):
        async def events(*args):
            yield 'event: projects_changed\ndata: {}\n\n'
        with patch.object(self.api, "project_event_stream", events):
            response = self.client.get("/api/projects/events")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/event-stream", response.headers["content-type"])
        self.assertEqual(response.headers["x-accel-buffering"], "no")
        self.assertIn("projects_changed", response.text)


class ProjectEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_emits_only_changes_and_stops_when_session_expires(self):
        request = Mock(is_disconnected=AsyncMock(return_value=False))
        user = Mock(side_effect=[{"id": "u"}, {"id": "u"}, {"id": "u"}, None])
        states = [{"revision": "a", "members_revision": "x"}, {"revision": "a", "members_revision": "x"}, {"revision": "b", "members_revision": "y"}]
        with patch.object(project_events, "project_update_state", side_effect=states), patch.object(project_events.asyncio, "sleep", new=AsyncMock()):
            events = [event async for event in project_events.project_event_stream(request, "u", user)]
        self.assertEqual(len(events), 3)
        self.assertIn('"revision": "a"', events[0])
        self.assertIn('"revision": "b"', events[1])
        self.assertIn("session_expired", events[2])

    async def test_stream_stops_on_disconnect_and_reports_retry_on_database_failure(self):
        request = Mock(is_disconnected=AsyncMock(return_value=True))
        self.assertEqual([event async for event in project_events.project_event_stream(request, "u", Mock())], [])
        request.is_disconnected = AsyncMock(return_value=False)
        with patch.object(project_events, "project_update_state", side_effect=RuntimeError("offline")), patch.object(project_events.logger, "warning"):
            events = [event async for event in project_events.project_event_stream(request, "u", Mock(return_value={"id": "u"}))]
        self.assertIn("retry_later", events[0])


if __name__ == "__main__":
    unittest.main()
