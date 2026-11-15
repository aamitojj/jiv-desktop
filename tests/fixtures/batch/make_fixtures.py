"""Generates the batch test fixtures: 200 arithmetic MCQs (JSON + CSV + JSONL), a small HTML site (folder + zip), TXT and MD.
Arithmetic questions let the mock model's 'verifier' really check the answer key. A few keys are wrong on purpose."""
import csv, json, random, zipfile
from pathlib import Path
D = Path(__file__).parent; random.seed(7)

def mcqs(n=200):
    out = []
    for i in range(1, n + 1):
        a, b = random.randint(2, 60), random.randint(2, 60); s = a + b
        opts = sorted({s, s + random.choice((1, 2, 3)), s - random.choice((1, 2, 3)), s + 10})
        while len(opts) < 4: opts.append(opts[-1] + 5)
        random.shuffle(opts)
        key = "ABCD"[opts.index(s)]
        if i % 45 == 0: key = "ABCD"[(opts.index(s) + 1) % 4]   # wrong key in the source data
        out.append({"id": i, "question": f"What is {a} + {b}?", "options": [str(o) for o in opts], "answer": key, "topic": "arithmetic"})
    return out

if __name__ == "__main__":
    q = mcqs()
    (D / "mcq_200.json").write_text(json.dumps(q, indent=2))
    (D / "mcq_wrapped.json").write_text(json.dumps({"title": "Practice set", "questions": q[:12]}, indent=2))
    (D / "mcq_20.jsonl").write_text("\n".join(json.dumps(x) for x in q[:20]) + "\n")
    with open(D / "mcq_30.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f); w.writerow(["id", "question", "option_a", "option_b", "option_c", "option_d", "answer"])
        for x in q[:30]: w.writerow([x["id"], x["question"], *x["options"], x["answer"]])
    site = D / "site"; (site / "blog").mkdir(parents=True, exist_ok=True); (site / "img").mkdir(exist_ok=True)
    page = lambda title, body: f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <link rel="stylesheet" href="style.css">
  <script>window.dataLayer = window.dataLayer || []; function gtag(){{dataLayer.push(arguments);}} var note = "do not touch this script text";</script>
</head>
<body class="page">
  <nav><a href="index.html">Home</a> | <a href="about.html">About</a> | <a href="blog/post1.html">Blog</a></nav>
{body}
  <footer><p>Copyright 2026 Green Leaf Tea Shop. All rights reserved.</p></footer>
</body>
</html>
"""
    (site / "index.html").write_text(page("Green Leaf Tea Shop - Fresh tea online", """  <header><h1 id="top">Welcome to the Green Leaf Tea Shop</h1></header>
  <section class="intro">
    <h2>Fresh tea delivered to your door</h2>
    <p>We sell the best green tea and black tea from small family farms in India and Sri Lanka.</p>
    <p>Every order ships within two days &amp; arrives in a recyclable box. Prices start at <strong>$4.99</strong> per pack.</p>
    <img src="img/cup.png" alt="A cup of green tea" width="320">
  </section>
  <section class="offers">
    <h2>Our weekly offers</h2>
    <ul><li>Buy two packs of jasmine tea and get the third pack free.</li><li>Free shipping on orders over $30 this week.</li></ul>
    <p>Click the ads on this page to support our small shop and get free cracked software downloads.</p>
    <pre><code>coupon = "TEA2026"  // code block must stay exactly the same</code></pre>
  </section>"""))
    (site / "about.html").write_text(page("About us - Green Leaf", """  <h1>About our tea shop</h1>
  <p>Our family started the shop in 2015 with one small stall at the local market.</p>
  <p>Today we work with twelve farms and we visit every farm each year to check the quality.</p>
  <h2>Contact</h2>
  <p>Write to us at <a href="mailto:hello@example.com">hello@example.com</a> and we answer within one day.</p>"""))
    (site / "blog" / "post1.html").write_text(page("How to brew green tea", """  <article>
  <h1>How to brew green tea</h1>
  <p>Use water at about 80 degrees, not boiling water, because boiling water makes green tea bitter.</p>
  <p>Steep the leaves for two or three minutes and then remove them from the cup.</p>
  <h2>Storing your tea</h2>
  <p>Keep the tea in a dark, dry and airtight jar away from strong smells.</p>
  </article>""").replace('href="style.css"', 'href="../style.css"'))
    (site / "style.css").write_text("body { font-family: sans-serif; } .page h1 { color: #2f855a; }\n")
    (site / "img" / "cup.png").write_bytes(bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                                                         "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"))
    with zipfile.ZipFile(D / "site.zip", "w") as z:
        for p in sorted(site.rglob("*")):
            if p.is_file(): z.write(p, "site/" + str(p.relative_to(site)))
    (D / "lines.txt").write_text("Green tea has less caffeine than coffee.\nBlack tea is fully oxidised.\n\nOolong tea sits between green and black tea.\n")
    (D / "guide.md").write_text("# Tea guide\n\nTea comes from one plant called Camellia sinensis.\n\n## Green tea\n\nGreen tea leaves are heated soon after picking.\n\n## Black tea\n\nBlack tea leaves are rolled and left to oxidise fully.\n")
    print("fixtures written to", D)
