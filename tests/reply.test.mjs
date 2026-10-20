// Build a reply the way the web client does, for tests/check_with_reference.py.
import { readFileSync } from "node:fs";
import { Article, Identity, MARKDOWN_TYPE, PURPOSE_ARTICLE, newArticle } from "../js/article.js";

const parent = Article.fromBytes(new Uint8Array(readFileSync(process.argv[2])));
const id = Identity.generate();
const refs = [...(parent.get("References") || "").split(/\s+/).filter(Boolean), parent.messageId];
const body = "someone wrote:\n" + parent.body.trimEnd().split("\n").map((l) => (l.startsWith(">") ? ">" + l : "> " + l)).join("\n") + "\n\nHello\n";
const art = newArticle(parent.group, "Re: test", body, {
  fromName: "web-entrance-test", references: refs,
  extra: [["Content-Type", MARKDOWN_TYPE], ["Message-Kind", "post"]],
});
art.set("Author-Key", id.keyText);
art.set("Signature", id.signText(PURPOSE_ARTICLE, art.canonical()));
art.set("Proof-Of-Work", "sha256; bits=0; nonce=AA");
art.set("Message-ID", art.computeMessageId());
console.error("js verify:", art.verify());
console.log(Buffer.from(art.toBytes()).toString("base64"));
