import os
from pathlib import Path

import certifi
from dotenv import load_dotenv
from pymongo import MongoClient

import dns.resolver

# Chỉ định Google DNS cho truy vấn DNS trong Python
resolver = dns.resolver.get_default_resolver()
resolver.nameservers = ["8.8.8.8", "8.8.4.4"]

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

# Đọc file .env ở thư mục gốc của project
PROJECT_DIR = Path(__file__).resolve().parent
load_dotenv(PROJECT_DIR / ".env")

MONGODB_URI = os.getenv("MONGODB_URI")
DB_NAME = os.getenv("MONGODB_DB_NAME", "tit_chatbot")

if not MONGODB_URI:
    raise RuntimeError("Không tìm thấy MONGODB_URI trong .env")

client = MongoClient(
    MONGODB_URI,
    serverSelectionTimeoutMS=10000,
)

try:
    # 1. Kiểm tra kết nối đến Atlas
    print("Testing connection...")
    print(client.admin.command("ping"))

    # 2. Thử ghi và đọc một document
    db = client[DB_NAME]
    collection = db["migration_test"]

    result = collection.insert_one({
        "message": "MongoDB connection works"
    })

    document = collection.find_one({
        "_id": result.inserted_id
    })

    print("Test document:", document)

    # 3. Xóa document thử nghiệm
    collection.delete_one({"_id": result.inserted_id})

    print("MongoDB connection successful!")

finally:
    client.close()