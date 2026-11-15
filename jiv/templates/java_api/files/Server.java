package {{ package }};

import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import java.io.IOException;
import java.io.OutputStream;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Instant;
import java.util.concurrent.Executors;

/**
 * {{ project_name }} — tiny REST API on the JDK HTTP server (no extra libraries).
 * GET  /health
 * GET  /items
 * POST /items   body: {"name":"..."}
 * Data is stored in data/items.json.
 */
public class Server {
    static final Path STORE = Path.of("data", "items.json");
    static final int PORT = {{ port }};

    public static void main(String[] args) throws Exception {
        Files.createDirectories(STORE.getParent());
        if (!Files.exists(STORE)) {
            Files.writeString(STORE, "[]");
        }
        HttpServer http = HttpServer.create(new InetSocketAddress("127.0.0.1", PORT), 0);
        http.createContext("/health", ex -> json(ex, 200, "{\"ok\":true,\"app\":\"{{ slug }}\"}"));
        http.createContext("/items", Server::items);
        http.setExecutor(Executors.newCachedThreadPool());
        http.start();
        System.out.println("{{ project_name }} listening on http://127.0.0.1:" + PORT + "  (GET /health, GET|POST /items)");
    }

    static void items(HttpExchange ex) throws IOException {
        String m = ex.getRequestMethod();
        if ("GET".equals(m)) {
            json(ex, 200, Files.readString(STORE));
        } else if ("POST".equals(m)) {
            String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8).trim();
            String name = extract(body, "name");
            if (name.isEmpty()) {
                json(ex, 400, "{\"error\":\"name required\"}");
                return;
            }
            synchronized (Server.class) {
                String arr = Files.readString(STORE).trim();
                if (!arr.startsWith("[")) {
                    arr = "[]";
                }
                String item = "{\"name\":\"" + escape(name) + "\",\"ts\":\"" + Instant.now() + "\"}";
                String next = "[]".equals(arr)
                        ? "[" + item + "]"
                        : arr.substring(0, arr.length() - 1) + "," + item + "]";
                Files.writeString(STORE, next);
                json(ex, 201, item);
            }
        } else {
            json(ex, 405, "{\"error\":\"method not allowed\"}");
        }
    }

    static void json(HttpExchange ex, int code, String body) throws IOException {
        byte[] b = body.getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().set("Content-Type", "application/json; charset=utf-8");
        ex.getResponseHeaders().set("Access-Control-Allow-Origin", "*");
        ex.sendResponseHeaders(code, b.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(b);
        }
    }

    static String extract(String json, String key) {
        String needle = "\"" + key + "\"";
        int i = json.indexOf(needle);
        if (i < 0) {
            return "";
        }
        int c = json.indexOf(':', i + needle.length());
        int q = json.indexOf('"', c + 1);
        if (q < 0) {
            return "";
        }
        int q2 = json.indexOf('"', q + 1);
        return q2 < 0 ? "" : json.substring(q + 1, q2);
    }

    static String escape(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"");
    }
}
