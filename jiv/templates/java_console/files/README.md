# {{ project_name }}

Java console app from Jiv Builder (no AI). Package: `{{ package }}`.

## Compile & run (JDK, no Maven)

```
javac -encoding UTF-8 -d out src/{{ package_path }}/Main.java
java -cp out {{ package }}.Main
```

Or use Builder → **Build** / **Run**. **Package** writes a runnable `dist/{{ slug }}.jar`.

## Maven (optional)

If you asked for a pom.xml and have Maven installed:

```
mvn -q -DskipTests package
java -jar target/{{ slug }}-1.0.0.jar
```

Maven may download plugins the first time you run it (needs the internet). javac does not.
