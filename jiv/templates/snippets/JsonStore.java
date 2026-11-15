// JsonStore: read/write a JSON array file (JDK only, no libraries).
import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;

public class JsonStore {
    private final Path file;
    public JsonStore(Path file) { this.file = file; }
    public synchronized String read() throws IOException {
        if (!Files.exists(file)) return "[]";
        return Files.readString(file);
    }
    public synchronized void write(String json) throws IOException {
        Files.createDirectories(file.getParent());
        Files.writeString(file, json);
    }
}
