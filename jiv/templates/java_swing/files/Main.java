package {{ package }};

import java.awt.*;
import javax.swing.*;

/** {{ project_name }} — Swing desktop app (Jiv Builder, no AI). */
public class Main {
    public static void main(String[] args) {
        SwingUtilities.invokeLater(Main::createAndShow);
    }

    private static void createAndShow() {
        JFrame f = new JFrame("{{ project_name }}");
        f.setDefaultCloseOperation(JFrame.EXIT_ON_CLOSE);
        f.setSize(720, 480);
        f.setLocationRelativeTo(null);

        CardLayout cards = new CardLayout();
        JPanel stack = new JPanel(cards);

        {% for s in screen_list %}
        JPanel s{{ s.n }} = new JPanel(new BorderLayout());
        s{{ s.n }}.add(new JLabel("{{ s.title }}", SwingConstants.CENTER), BorderLayout.CENTER);
        stack.add(s{{ s.n }}, "s{{ s.n }}");
        {% endfor %}

        JMenuBar bar = new JMenuBar();
        JMenu screens = new JMenu("Screens");
        {% for s in screen_list %}
        JMenuItem i{{ s.n }} = new JMenuItem("{{ s.title }}");
        i{{ s.n }}.addActionListener(e -> cards.show(stack, "s{{ s.n }}"));
        screens.add(i{{ s.n }});
        {% endfor %}
        JMenu file = new JMenu("File");
        JMenuItem quit = new JMenuItem("Quit");
        quit.addActionListener(e -> System.exit(0));
        file.add(quit);
        bar.add(file);
        bar.add(screens);
        f.setJMenuBar(bar);
        f.setContentPane(stack);
        cards.show(stack, "s1");
        f.setVisible(true);
    }
}
