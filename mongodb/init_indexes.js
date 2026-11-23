// mongosh "$MONGODB_URI" mongodb/init_indexes.js   — CLAUDE.md §6.1
db = db.getSiblingDB(process.env.MONGODB_DB || "ai_model_tracker");
db.raw_openrouter.createIndex({source: 1, request_key: 1, fetched_date: 1}, {unique: true});
db.raw_news.createIndex({source: 1, request_key: 1, fetched_date: 1}, {unique: true});
db.raw_comments.createIndex({source: 1, request_key: 1}, {unique: true});        // comment id
db.raw_lifecycle_pages.createIndex({source: 1, fetched_date: 1}, {unique: true});
db.raw_openrouter.createIndex({run_id: 1});
print("indexes ok on " + db.getName());
