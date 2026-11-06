import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic
import Dk 1.0

// Guided first-run setup: AI and brokerage, no terminal, no settings hunting.
Popup {
    id: wiz
    objectName: "setupWizard"
    modal: true
    focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(680, parent ? parent.width - 80 : 680)
    padding: 30
    closePolicy: Popup.CloseOnEscape
    Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, 0.65) }
    background: Rectangle { radius: 24; color: Theme.surface; border.color: Theme.borderStrong }

    property int step: 0
    readonly property var su: app.setupState
    readonly property var creds: app.credentialsInfo
    readonly property var cfg: app.settings
    property bool showCloud: false
    property string keyMsg: ""
    property bool keyOk: false
    property string brokerMsg: ""
    property bool brokerOk: false
    readonly property bool aiReady: su.done === true || (cfg.ai && cfg.ai.provider === "anthropic" && creds.anthropicSaved)
                                    || (cfg.ai && cfg.ai.decision_mode === "rules")

    onOpened: { step = 0; app.checkSetup() }
    function finish() { app.markFirstRunDone(); wiz.close() }

    Connections {
        target: app
        function onAnthropicKeyFinished(ok, message) {
            wiz.keyOk = ok; wiz.keyMsg = message
            if (ok) { app.setSetting("ai.provider", "anthropic"); app.setSetting("ai.anthropic_model", "claude-haiku-5-5"); app.setSetting("ai.decision_mode", "ai_confirm") }
        }
        function onConnectionTestFinished(env, ok, message) {
            if (env !== "paper") return
            wiz.brokerOk = ok; wiz.brokerMsg = message
            if (ok) { keyField.text = ""; secretField.text = "" }
        }
    }

    contentItem: ColumnLayout {
        spacing: 18

        // progress dots
        RowLayout {
            spacing: 8
            Repeater {
                model: ["hello", "ai", "brokerage", "done"]
                delegate: RowLayout {
                    spacing: 8
                    Rectangle {
                        width: index === wiz.step ? 26 : 8; height: 8; radius: 4
                        color: index <= wiz.step ? Theme.lime : Theme.surface3
                        Behavior on width { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
                    }
                }
            }
            Item { Layout.fillWidth: true }
            DkButton { compact: true; kind: "ghost"; text: "skip setup"; visible: wiz.step < 3; onClicked: wiz.finish() }
        }

        StackLayout {
            Layout.fillWidth: true
            currentIndex: wiz.step

            // ---------------------------------------------------------- 0 hello
            ColumnLayout {
                spacing: 14
                Txt { text: "hi. i’m dk — a careful little trading robot."; font.family: Theme.display; font.pixelSize: 24; font.weight: Font.Medium; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Txt {
                    text: "two quick steps and you’re set: give me an AI to think with, and (optionally) a free practice brokerage account. you can skip either and do it later."
                    color: Theme.textDim; font.pixelSize: 14; wrapMode: Text.WordWrap; elide: Text.ElideNone; Layout.fillWidth: true; lineHeight: 1.3
                }
                Repeater {
                    model: [
                        { icon: "flask", text: "right now you’re in demo mode: simulated prices, fake money." },
                        { icon: "shield", text: "safety rules on: $20 max per trade, 2 at once, $2 daily loss limit, no margin or shorting." },
                        { icon: "lock", text: "real money stays locked until you explicitly unlock it later." }
                    ]
                    delegate: RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        Rectangle { width: 32; height: 32; radius: 10; color: Theme.surface2
                            Icon { anchors.centerIn: parent; name: modelData.icon; size: 16; color: Theme.lime } }
                        Txt { text: modelData.text; color: Theme.textDim; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    }
                }
            }

            // ---------------------------------------------------------- 1 ai
            ColumnLayout {
                spacing: 14
                Txt { text: "step 1 · the ai"; font.family: Theme.display; font.pixelSize: 22; font.weight: Font.Medium }
                Txt {
                    text: "the AI double-checks every trade idea and explains its reasoning. the free option runs privately on this computer."
                    color: Theme.textDim; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true
                }
                Rectangle {
                    Layout.fillWidth: true
                    radius: 16
                    color: Theme.surface2
                    border.color: wiz.su.done ? Theme.lime : Theme.border
                    implicitHeight: localCol.implicitHeight + 32
                    ColumnLayout {
                        id: localCol
                        anchors.fill: parent
                        anchors.margins: 16
                        spacing: 10
                        RowLayout {
                            Icon { name: "ai"; size: 18; color: Theme.lime }
                            Txt { text: "free local AI"; font.weight: Font.DemiBold; font.pixelSize: 14 }
                            Pill { text: "RECOMMENDED"; kind: "lime"; fontSize: 10 }
                            Item { Layout.fillWidth: true }
                            Pill { visible: wiz.su.done === true; text: "READY"; kind: "solidLime"; icon: "check" }
                        }
                        Txt {
                            text: !wiz.su.checked ? "checking this computer…"
                                : wiz.su.done ? wiz.su.message
                                : (wiz.su.installed ? "Ollama is installed" + (wiz.su.running ? " and running." : " but not running.") : "installs Ollama (free, open source)")
                                  + (wiz.su.hasModel ? " the AI model is already downloaded." : " and downloads the " + wiz.su.model + " model — " + wiz.su.modelNote + ".")
                            color: Theme.textDim; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true
                        }
                        ColumnLayout {
                            visible: wiz.su.busy === true
                            Layout.fillWidth: true
                            spacing: 6
                            ThinBar { Layout.fillWidth: true; value: wiz.su.progress >= 0 ? wiz.su.progress : 0.05
                                SequentialAnimation on opacity { running: wiz.su.busy && wiz.su.progress < 0; loops: Animation.Infinite
                                    NumberAnimation { to: 0.4; duration: 600 } NumberAnimation { to: 1; duration: 600 } } }
                            Txt { text: wiz.su.message || ""; color: Theme.textMuted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        }
                        Txt { visible: (wiz.su.error || "") !== ""; text: wiz.su.error || ""; color: Theme.coral; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        RowLayout {
                            visible: wiz.su.done !== true
                            DkButton {
                                kind: "primary"; iconName: "bolt"
                                text: wiz.su.busy ? "working…" : "set it up for me"
                                enabled: wiz.su.checked && !wiz.su.busy && (wiz.su.canAutoInstall || wiz.su.installed)
                                onClicked: app.setupLocalAI()
                            }
                            DkButton {
                                visible: wiz.su.checked && !wiz.su.canAutoInstall && !wiz.su.installed
                                kind: "secondary"; text: "get Ollama"; onClicked: app.openUrl("https://ollama.com/download")
                            }
                            DkButton { kind: "ghost"; compact: true; text: "check again"; enabled: !wiz.su.busy; onClicked: app.checkSetup() }
                        }
                    }
                }
                DkButton { kind: "ghost"; compact: true; text: wiz.showCloud ? "hide cloud option" : "or use Claude in the cloud (paid, about $0.001 per decision) →"; onClicked: wiz.showCloud = !wiz.showCloud }
                ColumnLayout {
                    visible: wiz.showCloud
                    Layout.fillWidth: true
                    spacing: 8
                    RowLayout {
                        Layout.fillWidth: true
                        DkField { id: cloudKey; Layout.fillWidth: true; secret: true; placeholderText: wiz.creds.anthropicSaved ? "key saved ✓" : "paste your Anthropic API key" }
                        DkButton { kind: "secondary"; text: "get a key"; onClicked: app.openUrl("https://console.anthropic.com/settings/keys") }
                        DkButton { kind: "primary"; text: "connect"; enabled: cloudKey.text.length > 10; onClicked: { wiz.keyMsg = "checking…"; app.saveAnthropicKey(cloudKey.text) } }
                    }
                    Txt { visible: wiz.keyMsg !== ""; text: wiz.keyMsg; color: wiz.keyMsg === "checking…" ? Theme.textDim : wiz.keyOk ? Theme.lime : Theme.coral; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    Txt { text: "uses the low-cost Haiku model with a $5/month spending cap. change either in settings."; color: Theme.textMuted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                }
            }

            // ---------------------------------------------------------- 2 brokerage
            ColumnLayout {
                spacing: 14
                Txt { text: "step 2 · practice brokerage (optional)"; font.family: Theme.display; font.pixelSize: 22; font.weight: Font.Medium; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Txt {
                    text: "a free Alpaca paper account gives the app real market prices with fake money. no deposit, no card."
                    color: Theme.textDim; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true
                }
                Repeater {
                    model: [
                        { n: "1", t: "create a free account", b: "open Alpaca", u: "https://app.alpaca.markets/signup" },
                        { n: "2", t: "on the paper-trading home page, click “generate API keys”", b: "open dashboard", u: "https://app.alpaca.markets/paper/dashboard/overview" },
                        { n: "3", t: "paste both keys below", b: "", u: "" }
                    ]
                    delegate: RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        Rectangle { width: 24; height: 24; radius: 12; color: Theme.limeSoft
                            Txt { anchors.centerIn: parent; text: modelData.n; color: Theme.lime; font.pixelSize: 12; font.weight: Font.Bold } }
                        Txt { text: modelData.t; font.pixelSize: 13; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        DkButton { visible: modelData.b !== ""; compact: true; kind: "secondary"; text: modelData.b; onClicked: app.openUrl(modelData.u) }
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    DkField { id: keyField; Layout.fillWidth: true; placeholderText: "API key ID" }
                    DkField { id: secretField; Layout.fillWidth: true; placeholderText: "secret key"; secret: true }
                }
                RowLayout {
                    DkButton {
                        kind: "primary"; iconName: "plug"; text: "connect"
                        enabled: keyField.text.length > 5 && secretField.text.length > 5 && wiz.brokerMsg !== "testing…"
                        onClicked: { wiz.brokerMsg = "testing…"; app.saveAlpacaKeys("paper", keyField.text, secretField.text) }
                    }
                    Pill { visible: wiz.creds.paperSaved; text: "CONNECTED"; kind: "solidLime"; icon: "check" }
                }
                Txt { visible: wiz.brokerMsg !== ""; text: wiz.brokerMsg; color: wiz.brokerMsg === "testing…" ? Theme.textDim : wiz.brokerOk ? Theme.lime : Theme.coral; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                Txt { text: "keys are stored in your computer’s secure keychain, never in a file."; color: Theme.textMuted; font.pixelSize: 12 }
            }

            // ---------------------------------------------------------- 3 done
            ColumnLayout {
                spacing: 14
                Txt { text: "you’re set."; font.family: Theme.display; font.pixelSize: 26; font.weight: Font.Medium }
                Repeater {
                    model: [
                        { ok: wiz.aiReady, t: wiz.su.done ? "AI: free local model on this computer" : (wiz.cfg.ai && wiz.cfg.ai.provider === "anthropic" && wiz.creds.anthropicSaved ? "AI: Claude in the cloud" : "AI: not set up — trading will wait for it (or use rules-only in settings)") },
                        { ok: wiz.creds.paperSaved, t: wiz.creds.paperSaved ? "brokerage: Alpaca paper account connected" : "brokerage: none yet — staying in demo mode" },
                        { ok: true, t: "safety rules: on (change them in settings)" }
                    ]
                    delegate: RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Icon { name: modelData.ok ? "check" : "alert"; size: 16; color: modelData.ok ? Theme.lime : Theme.amber; stroke: 2.2 }
                        Txt { text: modelData.t; font.pixelSize: 14; color: Theme.textDim; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    }
                }
                Txt {
                    visible: wiz.creds.paperSaved && app.mode === "demo"
                    text: "switch to paper trading now? it uses real prices with fake money, and runs during market hours (9:30–4:00 ET)."
                    color: Theme.text; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true
                }
                Txt { text: "you can run this setup again any time from settings."; color: Theme.textMuted; font.pixelSize: 12 }
            }
        }

        // navigation
        RowLayout {
            Layout.fillWidth: true
            DkButton { visible: wiz.step > 0 && wiz.step < 3; kind: "ghost"; text: "back"; onClicked: wiz.step-- }
            Item { Layout.fillWidth: true }
            DkButton { visible: wiz.step === 1 && !wiz.aiReady; kind: "ghost"; text: "skip for now"; onClicked: wiz.step++ }
            DkButton { visible: wiz.step === 2 && !wiz.creds.paperSaved; kind: "ghost"; text: "stay in demo"; onClicked: wiz.step++ }
            DkButton {
                visible: wiz.step < 3 && !(wiz.step === 1 && !wiz.aiReady) && !(wiz.step === 2 && !wiz.creds.paperSaved)
                kind: "primary"; iconName: "arrowRight"; text: wiz.step === 0 ? "let’s go" : "next"
                onClicked: wiz.step++
            }
            DkButton { visible: wiz.step === 3 && wiz.creds.paperSaved && app.mode === "demo"; kind: "secondary"; text: "switch to paper"; onClicked: { app.setMode("paper", false); wiz.finish() } }
            DkButton { visible: wiz.step === 3; kind: "primary"; iconName: "check"; text: "done"; onClicked: wiz.finish() }
        }
    }
}
