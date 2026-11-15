# {{ project_name }}

HTML frontend + Java HTTP backend. Items are stored in `data/items.json` (no database driver to download). H2/SQLite would need a JDBC jar; this template stays fully offline.

## Run
1. Build and **Run** the Java `Server` (listens on 127.0.0.1:{{ port }}).
2. **Open site** to preview `frontend/` (or open `frontend/index.html` after the API is up).

```
javac -encoding UTF-8 -d out src/{{ package_path }}/Server.java
java -cp out {{ package }}.Server
```
