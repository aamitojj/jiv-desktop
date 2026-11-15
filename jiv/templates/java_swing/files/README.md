# {{ project_name }}

Swing desktop app from Jiv Builder (no AI). Screens: {{ screens }}.

```
javac -encoding UTF-8 -d out src/{{ package_path }}/Main.java
java -cp out {{ package }}.Main
```

Use Builder → Build / Run. Package writes `dist/{{ slug }}.jar`. An `.exe` is only produced if this JDK has `jpackage` (and, on Windows, often the WiX toolset); otherwise use the jar: `java -jar dist/{{ slug }}.jar`.
