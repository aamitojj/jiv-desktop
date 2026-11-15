package {{ package }};

/**
 * {{ project_name }} — console entry point (Jiv Builder, no AI).
 */
public class Main {
    public static void main(String[] args) {
        System.out.println("Hello from {{ project_name }}!");
        if (args.length == 0) {
            System.out.println("Pass arguments to echo them back.");
            return;
        }
        System.out.println("You said: " + String.join(" ", args));
    }
}
