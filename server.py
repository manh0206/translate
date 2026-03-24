const SERVER_STT = "http://localhost:5000/stt";
const SERVER_GEMINI = "http://localhost:5000/gemini";


// Speech to text
function initGUI() {
    if (!document.body) {
        requestAnimationFrame(initGUI);
        return;
    }

    // ===== GUI ANSWER VIEWER (GLASS MICRO - COMPLETE) =====
    let answerLines = [];
    let currentIndex = 0;
    let guiVisible = false;

    const oldGui = document.getElementById("mini-answer-gui");
    if (oldGui) oldGui.remove();

    if (window.__miniAnswerKeyHandler) {
        window.removeEventListener("keydown", window.__miniAnswerKeyHandler, true);
    }

    const guiBox = document.createElement("div");
    guiBox.id = "mini-answer-gui";

    Object.assign(guiBox.style, {
        position: "fixed",
        bottom: "6px",
        right: "6px",
        background: "rgba(0,0,0,0.28)",
        backdropFilter: "blur(6px)",
        webkitBackdropFilter: "blur(6px)",
        color: "#00ff88",
        padding: "2px 6px",
        fontSize: "11px",
        fontFamily: "monospace",
        borderRadius: "6px",
        border: "1px solid rgba(255,255,255,0.12)",
        zIndex: "999999",
        display: "none",
        width: "fit-content",
        whiteSpace: "nowrap",
        lineHeight: "1",
        boxShadow: "0 0 8px rgba(0,255,150,0.12)",
        pointerEvents: "none",
        transition: "opacity 0.15s ease"
    });

    document.body.appendChild(guiBox);

    window.showAnswerGUI = function(answerText) {
        answerLines = answerText
            .split("\n")
            .map(l => l.trim())
            .filter(Boolean);

        currentIndex = 0;
        guiVisible = true;
        guiBox.style.display = "block";
        renderLine();
    };

    function renderLine() {
        if (!answerLines.length) return;
        guiBox.textContent = answerLines[currentIndex];
    }

    window.__miniAnswerKeyHandler = function (e) {
        if (!answerLines.length) return;

        if (e.key === "ArrowRight" && currentIndex < answerLines.length - 1) {
            currentIndex++;
            renderLine();
            e.stopImmediatePropagation();
        }

        else if (e.key === "ArrowLeft" && currentIndex > 0) {
            currentIndex--;
            renderLine();
            e.stopImmediatePropagation();
        }

        else if (e.key === "\\") {
            guiVisible = !guiVisible;
            guiBox.style.display = guiVisible ? "block" : "none";
            e.stopImmediatePropagation();
        }
    };

    window.addEventListener("keydown", window.__miniAnswerKeyHandler, true);
}

initGUI();
async function speechToTextFromUrl(url) {

    try {

        const res = await fetch(SERVER_STT, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ url })
        });

        const data = await res.json();

        return data?.text || "(không nhận diện được)";

    } catch (e) {

        console.error("STT error:", e);

        return "(STT lỗi)";
    }

}


// Gemini
async function askGemini(prompt) {

    try {

        const res = await fetch(SERVER_GEMINI, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ prompt })
        });

        const data = await res.json();

        return data?.answer || "(AI không trả lời)";

    } catch (e) {

        console.error("Gemini error:", e);

        return "(Gemini lỗi)";
    }

}


// xử lý getinfo
async function handleGetinfo(jsonText) {

    try {

        const data = JSON.parse(jsonText);

        if (!data?.data?.game?.question) return;

        const questions = data.data.game.question;
        const subjContent = data.data.game.Subject?.content || "";

        let fullPrompt = "Solve the following questions:\n\n";

        for (let i = 0; i < questions.length; i++) {

            const q = questions[i];

            const question = q.content?.content || "(không có câu hỏi)";
            const options = (q.ans || []).map(a => a.content);

            let info = "";
            const desc = q.Description?.content || "";

            if (desc.startsWith("http")) {

                info = await speechToTextFromUrl(desc);

            } else if (desc.trim()) {

                info = desc.trim();

            } else if (subjContent.startsWith("http")) {

                info = await speechToTextFromUrl(subjContent);

            } else {

                info = subjContent || "(không có audio/text)";

            }

            console.log(`Q${i+1}: ${question}`);
            console.log(`Info: ${info}`);

            fullPrompt += `Q${i+1}: ${question}\n`;

            if (info) {
                fullPrompt += `Info: ${info}\n`;
            }

            options.forEach((opt, idx) => {

                fullPrompt += `${String.fromCharCode(65+idx)}. ${opt}\n`;

            });

            fullPrompt += "\n";

        }

        console.log("Sending to Gemini...");

        const answer = await askGemini(fullPrompt);

        showAnswerGUI(answer);

    } catch (e) {

        console.error("Parse error:", e);

    }

}


// GUI hiển thị
function showAnswerGUI(text) {

    let box = document.getElementById("ai-answer-box");

    if (!box) {

        box = document.createElement("div");

        box.id = "ai-answer-box";

        box.style.position = "fixed";
        box.style.top = "20px";
        box.style.right = "20px";
        box.style.width = "300px";
        box.style.background = "black";
        box.style.color = "white";
        box.style.padding = "10px";
        box.style.zIndex = 999999;
        box.style.fontSize = "14px";
        box.style.borderRadius = "8px";

        document.body.appendChild(box);

    }

    box.textContent = text;

}


// nhận data từ background
chrome.runtime.onMessage.addListener((msg) => {

    if (msg.type === "GETINFO_DATA") {

        console.log("Getinfo received");

        handleGetinfo(msg.json);

    }

});
