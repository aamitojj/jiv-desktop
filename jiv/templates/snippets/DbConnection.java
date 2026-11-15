// DbConnection: documents how you would open SQLite/H2 if you add a JDBC driver later.
// This project ships with a JSON file store so generation stays offline (no driver jar).
//
// Example once you have a driver on the classpath:
//   Class.forName("org.sqlite.JDBC");
//   Connection c = DriverManager.getConnection("jdbc:sqlite:data/app.db");
public class DbConnection {
    private DbConnection() {}
    public static String note() {
        return "Add a JDBC driver yourself to use SQLite or H2. Default store is data/*.json.";
    }
}
