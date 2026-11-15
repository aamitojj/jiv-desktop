# {{ project_name }}

REST API on `com.sun.net.httpserver` (built into the JDK). No Spring Boot and no Maven — so nothing is downloaded when you generate or compile this project.

```
javac -encoding UTF-8 -d out src/{{ package_path }}/Server.java
java -cp out {{ package }}.Server
```

Then: `GET http://127.0.0.1:{{ port }}/health` and `GET|POST /items`. Data: `data/items.json`.

Spring Boot is intentionally not used here: it would pull many dependencies the first time Maven runs. If you want Spring later, add it yourself with a Maven project you already have set up.
