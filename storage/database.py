"""Backward-compatible persistence facade; implementations live by domain."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any

import bcrypt
from bson import ObjectId

from storage.mongo_client import ensure_indexes, get_collection
from storage._helpers import (
    _conv_to_obj,
    _now,
    _public_user,
    _to_object_id,
    hash_password,
    logger,
    verify_password,
)
from storage.users import (
    authenticate_user,
    create_user,
    delete_user,
    get_user_by_id,
    get_user_by_username,
    list_user_thread_ids,
    list_users,
    set_user_active,
    update_user_profile,
)
from storage.sessions import create_session, delete_session, get_session
from storage.conversations import (
    create_or_update_conversation,
    delete_conversation,
    get_admin_conversation_messages,
    get_chat_history,
    get_conversation,
    list_admin_conversations,
    list_conversations,
    save_chat_message,
    toggle_pin_conversation,
    update_conversation_title,
)
from storage.memories import (
    _cosine_similarity,
    _generate_embedding,
    delete_memory_by_id,
    get_user_memories,
    save_memory,
    update_memory_by_id,
)


def init_db() -> None:
    ensure_indexes()
