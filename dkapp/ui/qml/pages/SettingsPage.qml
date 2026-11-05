import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic
import Dk 1.0

DkScroll {
    id: page
    maxContentWidth: 980
    readonly property var cfg: app.settings
    readonly property var creds: app.credentialsInfo
    readonly property var live: app.liveAuth
    readonly property var info: app.staticInfo
    readonly property var st: app.state
    property string paperMsg: ""
    property bool paperOk: false
    property string liveMsg: ""
    property bool liveOk: false
    property string aiMsg: ""
    property bool aiOk: false
    property string keyMsg: ""
    property string modeMsg: ""
    property string pendingMode: ""
    property var acks: []
    property string authMsg: ""
    property var paperKey: null
    property var paperSecret: null
    property var liveKey: null
    property var liveSecret: null

    function set(path, value) {
        var err = app.setSetting(path, value)
        if (err !== "") app.toast("warning", "not saved", err)
    }
    function trySetMode(m) {
        var err = app.setMode(m, false)
        if (err === "open_positions") { page.pendingMode = m; switchDialog.open() }
        else if (err !== "") page.modeMsg = err
        else page.modeMsg = ""
    }

    Connections {
        target: app
        function onConnectionTestFinished(env, ok, message) {
            if (env === "paper") { page.paperOk = ok; page.paperMsg = message; if (ok) { paperKey.text = ""; paperSecret.text = "" } }
            else { page.liveOk = ok; page.liveMsg = message; if (ok) { liveKey.text = ""; liveSecret.text = "" } }
        }
        function onAiTestFinished(ok, message) { page.aiOk = ok; page.aiMsg = message }
        function onAnthropicKeyFinished(ok, message) { page.aiOk = ok; page.keyMsg = message; if (ok) anthropicKey.text = "" }
    }
    Component.onCompleted: app.refreshOllamaModels()

    ColumnLayout {
        width: parent.width
        spacing: Theme.gap

        // ================================================================ trading mode
        Card {
            Layout.fillWidth: true
            title: "trading mode"
            icon: "bolt"
            subtitle: "demo uses a simulator. paper uses Alpaca's free practice account with real prices. live uses real money and is locked until you authorize it."
            Segmented {
                options: [{ value: "demo", label: "demo" }, { value: "paper", label: "paper (practice)" }, { value: "live", label: "live (real money)" }]
                current: app.mode
                onPicked: (v) => page.trySetMode(v)
            }
            Txt { visible: page.modeMsg !== ""; text: page.modeMsg; color: Theme.amber; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true }

            // live authorization
            Rectangle {
                Layout.fillWidth: true
                Layout.topMargin: 6
                radius: 14
                color: page.live.authorized ? Theme.coralSoft : Theme.surface2
                border.color: page.live.authorized ? Theme.coral : Theme.border
                implicitHeight: authCol.implicitHeight + 32
                ColumnLayout {
                    id: authCol
                    anchors.fill: parent
                    anchors.margins: 16
                    spacing: 10
                    RowLayout {
                        Icon { name: "lock"; size: 16; color: page.live.authorized ? Theme.coral : Theme.textDim }
                        Txt { text: page.live.authorized ? "real-money trading is authorized" : "real-money trading is locked"; font.weight: Font.DemiBold; font.pixelSize: 14 }
                        Item { Layout.fillWidth: true }
                        DkButton { visible: page.live.authorized; compact: true; kind: "danger"; text: "revoke"; onClicked: app.revokeLive() }
                    }
                    Txt { text: page.live.message; color: Theme.textDim; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                    ColumnLayout {
                        visible: !page.live.authorized
                        Layout.fillWidth: true
                        spacing: 8
                        readonly property var paper: app.paperSummary()
                        Txt {
                            text: "your paper results so far: " + parent.paper.trades + " trades over " + parent.paper.days + " days, net " + Theme.signedMoney(parent.paper.total) + ", win rate " + Theme.pct(parent.paper.winRate * 100, 0) + ". strategies with real backtest evidence: " + (parent.paper.validated.length ? parent.paper.validated.join(", ") : "none yet") + "."
                            color: Theme.text; font.pixelSize: 13; wrapMode: Text.WordWrap; elide: Text.ElideNone; Layout.fillWidth: true
                        }
                        Txt { text: "we suggest at least 20 trading days of paper results first. only strategies with real backtest evidence will trade real money."; color: Theme.textMuted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        Repeater {
                            model: page.live.acks || []
                            delegate: RowLayout {
                                Layout.fillWidth: true
                                spacing: 10
                                DkSwitch {
                                    checked: page.acks.indexOf(modelData) >= 0
                                    onToggled: {
                                        var a = page.acks.slice()
                                        var i = a.indexOf(modelData)
                                        if (checked && i < 0) a.push(modelData)
                                        if (!checked && i >= 0) a.splice(i, 1)
                                        page.acks = a
                                    }
                                }
                                Txt { text: modelData; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true; color: Theme.textDim }
                            }
                        }
                        Txt { text: "type: " + page.live.phrase; color: Theme.textMuted; font.pixelSize: 12 }
                        RowLayout {
                            Layout.fillWidth: true
                            DkField { id: phrase; Layout.fillWidth: true; placeholderText: page.live.phrase }
                            DkButton {
                                kind: "danger"; text: "authorize live trading"
                                enabled: phrase.text.toLowerCase().trim() === page.live.phrase && page.acks.length === (page.live.acks || []).length && page.creds.liveSaved
                                onClicked: { page.authMsg = app.authorizeLive(phrase.text, page.acks); if (page.authMsg === "") { phrase.text = ""; page.acks = [] } }
                            }
                        }
                        Txt { visible: !page.creds.liveSaved; text: "save and test your live Alpaca keys below first."; color: Theme.amber; font.pixelSize: 12 }
                        Txt { visible: page.authMsg !== ""; text: page.authMsg; color: Theme.coral; font.pixelSize: 12 }
                    }
                }
            }
        }

        // ================================================================ brokerage
        Card {
            Layout.fillWidth: true
            title: "brokerage — Alpaca"
            icon: "plug"
            subtitle: "keys are stored in your computer's secure credential store (" + (page.creds.secureStore ? page.creds.storeName.split(".").pop() : "unavailable") + "), never in plain files. get free paper keys at app.alpaca.markets → paper trading → API keys."
            Txt { visible: !page.creds.secureStore; text: "no secure credential store found on this computer, so keys can't be saved: " + page.creds.storeName; color: Theme.coral; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            Repeater {
                model: [{ env: "paper", label: "paper trading keys", saved: page.creds.paperSaved }, { env: "live", label: "live trading keys (real money)", saved: page.creds.liveSaved }]
                delegate: ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    RowLayout {
                        Txt { text: modelData.label; font.pixelSize: 14; font.weight: Font.Medium }
                        Pill { text: modelData.saved ? "SAVED" : "NOT SET"; kind: modelData.saved ? "lime" : "neutral" }
                        Item { Layout.fillWidth: true }
                        DkButton { visible: modelData.saved; compact: true; kind: "ghost"; text: "remove"; onClicked: app.deleteAlpacaKeys(modelData.env) }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        DkField { id: kf; Layout.fillWidth: true; placeholderText: "API key ID"; Component.onCompleted: if (modelData.env === "paper") paperKey = kf; else liveKey = kf }
                        DkField { id: sf; Layout.fillWidth: true; placeholderText: "secret key"; secret: true; Component.onCompleted: if (modelData.env === "paper") paperSecret = sf; else liveSecret = sf }
                        DkButton { text: "test & save"; kind: "primary"; enabled: kf.text.length > 5 && sf.text.length > 5; onClicked: { if (modelData.env === "paper") page.paperMsg = "testing…"; else page.liveMsg = "testing…"; app.saveAlpacaKeys(modelData.env, kf.text, sf.text) } }
                    }
                    Txt {
                        readonly property string m: modelData.env === "paper" ? page.paperMsg : page.liveMsg
                        readonly property bool ok: modelData.env === "paper" ? page.paperOk : page.liveOk
                        visible: m !== ""
                        text: m
                        color: m === "testing…" ? Theme.textDim : (ok ? Theme.lime : Theme.coral)
                        font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true
                    }
                }
            }
        }

        // ================================================================ AI
        Card {
            Layout.fillWidth: true
            title: "ai"
            icon: "ai"
            subtitle: "the AI only reviews setups the strategies already found. if it's offline or answers badly, no AI-dependent trade happens."
            SettingRow {
                label: "who decides entries"
                help: page.cfg.ai.decision_mode === "rules" ? "strategies trade on their own (the risk engine still checks everything)." : "every entry needs the AI's approval and enough confidence."
                Segmented { small: true; options: [{ value: "ai_confirm", label: "rules + ai" }, { value: "rules", label: "rules only" }]; current: page.cfg.ai.decision_mode; onPicked: (v) => page.set("ai.decision_mode", v) }
            }
            SettingRow {
                label: "ai provider"
                help: page.cfg.ai.provider === "ollama" ? "Ollama runs a model on this computer — free and private. install it from ollama.com, then run: ollama pull llama3.1:8b" : "Claude in the cloud. billed per use by Anthropic; your monthly budget below is a hard cap."
                Segmented { small: true; options: [{ value: "ollama", label: "local (free)" }, { value: "anthropic", label: "cloud (paid)" }]; current: page.cfg.ai.provider; onPicked: (v) => page.set("ai.provider", v) }
            }
            ColumnLayout {
                visible: page.cfg.ai.provider === "ollama"
                Layout.fillWidth: true
                spacing: 12
                SettingRow {
                    label: "ollama address"
                    DkField { width: 260; text: page.cfg.ai.ollama_url; onEditingFinished: page.set("ai.ollama_url", text) }
                }
                SettingRow {
                    label: "model"
                    help: app.ollamaModels.length ? "installed models on this computer" : "no models found — is Ollama running?"
                    DkCombo {
                        width: 260
                        model: app.ollamaModels.length ? app.ollamaModels : [page.cfg.ai.ollama_model]
                        currentIndex: Math.max(0, (app.ollamaModels.length ? app.ollamaModels : [page.cfg.ai.ollama_model]).indexOf(page.cfg.ai.ollama_model))
                        onActivated: (i) => page.set("ai.ollama_model", currentText)
                    }
                    DkButton { compact: true; kind: "ghost"; iconName: "refresh"; text: ""; onClicked: app.refreshOllamaModels() }
                }
            }
            ColumnLayout {
                visible: page.cfg.ai.provider === "anthropic"
                Layout.fillWidth: true
                spacing: 12
                SettingRow {
                    label: "Anthropic API key"
                    help: page.creds.anthropicSaved ? "saved in your secure credential store." : "create one at console.anthropic.com. stored securely, never in files."
                    DkField { id: anthropicKey; width: 260; secret: true; placeholderText: page.creds.anthropicSaved ? "••••••• saved" : "sk-ant-…" }
                    DkButton { compact: true; kind: "primary"; text: "test & save"; enabled: anthropicKey.text.length > 10; onClicked: { page.keyMsg = "testing…"; app.saveAnthropicKey(anthropicKey.text) } }
                }
                Txt { visible: page.keyMsg !== ""; text: page.keyMsg; color: page.keyMsg === "testing…" ? Theme.textDim : page.aiOk ? Theme.lime : Theme.coral; font.pixelSize: 13; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                SettingRow {
                    label: "model"
                    help: {
                        var m = page.info.anthropicModels.filter(function(x) { return x.id === page.cfg.ai.anthropic_model })[0]
                        if (!m) return ""
                        var per = (1800 * m.input + 900 * m.output) / 1e6
                        return "$" + m.input.toFixed(2) + " / $" + m.output.toFixed(2) + " per million tokens in/out · roughly " + Theme.money(per, 4) + " per decision"
                    }
                    DkCombo {
                        width: 300
                        textRole: "label"
                        model: page.info.anthropicModels
                        currentIndex: Math.max(0, page.info.anthropicModels.map(function(x) { return x.id }).indexOf(page.cfg.ai.anthropic_model))
                        onActivated: (i) => page.set("ai.anthropic_model", page.info.anthropicModels[i].id)
                    }
                }
                SettingRow {
                    label: "monthly budget"
                    help: "cloud AI stops (and AI-dependent trades pause) once this month's spend reaches the cap."
                    DkNumber { value: page.cfg.ai.monthly_budget_usd; prefix: "$"; decimals: 2; step: 1; from: 0; to: 500; onCommitted: (v) => page.set("ai.monthly_budget_usd", v) }
                }
            }
            SettingRow {
                label: "minimum confidence"
                tip: "confidence"
                help: "the AI must be at least this sure (and rate the evidence moderate or strong)."
                DkNumber { value: Math.round(page.cfg.ai.min_confidence * 100); suffix: "%"; step: 5; from: 30; to: 95; onCommitted: (v) => page.set("ai.min_confidence", v / 100) }
            }
            SettingRow {
                label: "ai checks open positions"
                help: "every " + page.cfg.ai.review_interval_minutes + " minutes the AI can suggest selling early (it can only reduce risk)."
                DkSwitch { checked: page.cfg.ai.review_open_positions; onToggled: page.set("ai.review_open_positions", checked) }
            }
            RowLayout {
                DkButton { compact: true; kind: "secondary"; iconName: "refresh"; text: "test ai connection"; onClicked: { page.aiMsg = "checking…"; app.testAI() } }
                Txt { text: page.aiMsg; color: page.aiMsg === "checking…" ? Theme.textDim : page.aiOk ? Theme.lime : Theme.coral; font.pixelSize: 13; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            }
        }

        // ================================================================ safety
        Card {
            Layout.fillWidth: true
            title: "safety rules"
            icon: "shield"
            tip: "risk engine"
            subtitle: "dollar amounts are at your starting capital and scale with the account when reinvesting. raising any limit pauses live trading until you re-authorize."
            SettingRow { label: "starting capital"; help: "the amount the limits below are based on."
                DkNumber { value: page.cfg.risk.starting_capital; prefix: "$"; decimals: 0; step: 10; from: 10; to: 1000000; onCommitted: (v) => page.set("risk.starting_capital", v) } }
            SettingRow { label: "max per trade"; tip: "position"; help: "the most any single position can be worth when bought."
                DkNumber { value: page.cfg.risk.max_position_usd; prefix: "$"; decimals: 2; step: 1; from: 1; to: 100000; onCommitted: (v) => page.set("risk.max_position_usd", v) } }
            SettingRow { label: "positions at once"
                DkNumber { value: page.cfg.risk.max_positions; step: 1; from: 1; to: 20; onCommitted: (v) => page.set("risk.max_positions", v) } }
            SettingRow { label: "daily loss limit"; tip: "daily loss limit"; help: "no new trades for the rest of the day once losses reach this."
                DkNumber { value: page.cfg.risk.daily_loss_limit_usd; prefix: "$"; decimals: 2; step: 0.5; from: 0.5; to: 100000; onCommitted: (v) => page.set("risk.daily_loss_limit_usd", v) } }
            SettingRow { label: "when the daily limit hits"; help: "also sell open positions immediately (otherwise they keep their stops)."
                DkSwitch { checked: page.cfg.risk.close_positions_on_daily_limit; onToggled: page.set("risk.close_positions_on_daily_limit", checked) } }
            SettingRow { label: "risk per trade"; tip: "stop-loss"; help: "max loss if a stop is hit, as % of the account. positions are shrunk to fit."
                DkNumber { value: page.cfg.risk.max_risk_per_trade_pct; suffix: "%"; decimals: 1; step: 0.1; from: 0.1; to: 10; onCommitted: (v) => page.set("risk.max_risk_per_trade_pct", v) } }
            SettingRow { label: "stop-loss distance"; help: "allowed range below the entry price. setups needing a wider stop are skipped."
                DkNumber { value: page.cfg.risk.min_stop_pct; suffix: "% min"; decimals: 1; step: 0.1; from: 0.1; to: 10; implicitWidth: 150; onCommitted: (v) => page.set("risk.min_stop_pct", v) }
                DkNumber { value: page.cfg.risk.max_stop_pct; suffix: "% max"; decimals: 1; step: 0.1; from: 0.2; to: 20; implicitWidth: 150; onCommitted: (v) => page.set("risk.max_stop_pct", v) } }
            SettingRow { label: "max spread"; tip: "spread"
                DkNumber { value: page.cfg.risk.max_spread_bps; suffix: " bps"; decimals: 0; step: 5; from: 1; to: 500; onCommitted: (v) => page.set("risk.max_spread_bps", v) } }
            SettingRow { label: "max trades per day"
                DkNumber { value: page.cfg.risk.max_trades_per_day; step: 1; from: 1; to: 100; onCommitted: (v) => page.set("risk.max_trades_per_day", v) } }
            SettingRow { label: "pause after losses in a row"; help: "take the rest of the day off after this many consecutive losing trades."
                DkNumber { value: page.cfg.risk.max_consecutive_losses; step: 1; from: 1; to: 20; onCommitted: (v) => page.set("risk.max_consecutive_losses", v) } }
            SettingRow { label: "shrink size after losses"; help: "each consecutive loss cuts the next position by 25% (never below half). risk is never increased to win money back."
                DkSwitch { checked: page.cfg.risk.reduce_size_after_losses; onToggled: page.set("risk.reduce_size_after_losses", checked) } }
            SettingRow { label: "spend only settled cash"; tip: "settled cash"; help: "recommended for small accounts: avoids good-faith violations entirely."
                Segmented { small: true; options: [{ value: "conservative", label: "settled only" }, { value: "broker", label: "trust broker" }]; current: page.cfg.risk.settlement_mode; onPicked: (v) => page.set("risk.settlement_mode", v) } }
            SettingRow { label: "price range"; help: "skip stocks priced outside this range."
                DkNumber { value: page.cfg.risk.min_price; prefix: "$"; decimals: 0; step: 1; from: 0; to: 1000; implicitWidth: 130; onCommitted: (v) => page.set("risk.min_price", v) }
                DkNumber { value: page.cfg.risk.max_price; prefix: "$"; decimals: 0; step: 50; from: 1; to: 100000; implicitWidth: 150; onCommitted: (v) => page.set("risk.max_price", v) } }
            SettingRow { label: "legacy day-trade guard"; tip: "pdt"; help: "the $25k pattern-day-trader rule was retired June 4, 2026. turn on only if your broker still enforces it."
                DkSwitch { checked: page.cfg.risk.pdt_guard; onToggled: page.set("risk.pdt_guard", checked) } }
            Txt { text: "always enforced and not configurable: no margin, no leverage, no short selling, no options, no crypto, no stale data, DAY orders only, every position closed before the market closes."; color: Theme.textMuted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true }
        }

        // ================================================================ compounding
        Card {
            Layout.fillWidth: true
            title: "money growth"
            icon: "coin"
            tip: "reinvest"
            Segmented { options: [{ value: "reinvest", label: "reinvest profits" }, { value: "fixed", label: "fixed allocation" }]; current: page.cfg.compounding.mode; onPicked: (v) => page.set("compounding.mode", v) }
            Txt {
                readonly property real start: page.cfg.risk.starting_capital
                readonly property real cap: page.cfg.risk.max_position_usd
                text: page.cfg.compounding.mode === "reinvest"
                    ? "sizes follow the account: at " + Theme.money(start * 1.05) + " the max per trade becomes " + Theme.money(cap * 1.05) + "; at " + Theme.money(start * 0.95) + " it shrinks to " + Theme.money(cap * 0.95) + "."
                    : "the app trades with up to " + Theme.money(page.cfg.compounding.fixed_allocation_usd) + ". profits above that are left alone; if the account drops below it, sizes shrink."
                color: Theme.textDim; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true
            }
            SettingRow {
                visible: page.cfg.compounding.mode === "fixed"
                label: "trading allocation"
                DkNumber { value: page.cfg.compounding.fixed_allocation_usd; prefix: "$"; decimals: 0; step: 10; from: 10; to: 1000000; onCommitted: (v) => page.set("compounding.fixed_allocation_usd", v) }
            }
        }

        // ================================================================ strategies
        Card {
            Layout.fillWidth: true
            title: "strategies"
            icon: "flask"
            subtitle: "turn strategies on or off. only strategies with real backtest evidence may trade real money."
            Repeater {
                model: page.info.strategies
                delegate: SettingRow {
                    label: modelData.name
                    help: modelData.explain
                    DkSwitch {
                        checked: page.cfg.strategies.enabled[modelData.id] === true
                        onToggled: { var e = app.setStrategyEnabled(modelData.id, checked); if (e !== "") app.toast("warning", "not saved", e) }
                    }
                }
            }
            SettingRow {
                label: "paper can trade untested strategies"
                help: "lets paper/demo trading gather forward-test evidence. never applies to real money."
                DkSwitch { checked: page.cfg.strategies.paper_allow_unvalidated; onToggled: page.set("strategies.paper_allow_unvalidated", checked) }
            }
        }

        // ================================================================ app behaviour
        Card {
            Layout.fillWidth: true
            title: "app behavior"
            icon: "settings"
            SettingRow { label: "keep running in the " + (Qt.platform.os === "osx" ? "menu bar" : "system tray"); help: "closing the window keeps trading going in the background."
                DkSwitch { checked: page.cfg.automation.minimize_to_tray; onToggled: page.set("automation.minimize_to_tray", checked) } }
            SettingRow { label: "launch when i log in"; help: "starts quietly in the tray so it's ready at the market open."
                DkSwitch { checked: page.cfg.automation.launch_at_login; onToggled: page.set("automation.launch_at_login", checked) } }
            SettingRow { label: "start minimized"
                DkSwitch { checked: page.cfg.automation.start_minimized; onToggled: page.set("automation.start_minimized", checked) } }
            SettingRow { label: "keep the computer awake during market hours"; help: "prevents idle sleep while the market is open. closing a laptop lid usually still sleeps it — and a sleeping computer can't trade."
                DkSwitch { checked: page.cfg.automation.keep_awake_during_market; onToggled: page.set("automation.keep_awake_during_market", checked) } }
            SectionLabel { text: "desktop notifications"; Layout.topMargin: 4 }
            SettingRow { label: "completed trades"; DkSwitch { checked: page.cfg.automation.notify_trades; onToggled: page.set("automation.notify_trades", checked) } }
            SettingRow { label: "important decisions"; DkSwitch { checked: page.cfg.automation.notify_decisions; onToggled: page.set("automation.notify_decisions", checked) } }
            SettingRow { label: "errors"; DkSwitch { checked: page.cfg.automation.notify_errors; onToggled: page.set("automation.notify_errors", checked) } }
            SettingRow { label: "risk-limit events"; DkSwitch { checked: page.cfg.automation.notify_risk; onToggled: page.set("automation.notify_risk", checked) } }
        }

        // ================================================================ data
        Card {
            Layout.fillWidth: true
            title: "market data"
            icon: "data"
            SettingRow {
                label: "data feed"; tip: "iex"
                help: page.cfg.data.feed === "iex" ? "free. real-time prices from the IEX exchange only." : "requires Alpaca's paid Algo Trader Plus plan (about $99/month). don't pick this unless you've subscribed."
                Segmented { small: true; options: [{ value: "iex", label: "IEX (free)" }, { value: "sip", label: "SIP (paid)" }]; current: page.cfg.data.feed; onPicked: (v) => page.set("data.feed", v) }
            }
            SettingRow { label: "stocks to scan"; help: "takes effect the next time the engine starts."
                DkNumber { value: page.cfg.data.universe_size; step: 50; from: 50; to: 600; onCommitted: (v) => page.set("data.universe_size", v) } }
            SettingRow { label: "watch up close"; help: "how many top candidates get minute-by-minute analysis."
                DkNumber { value: page.cfg.data.hotlist_size; step: 5; from: 5; to: 60; onCommitted: (v) => page.set("data.hotlist_size", v) } }
            SettingRow { label: "monthly data cost"; help: "only if you pay for a data plan — included in the cost report."
                DkNumber { value: page.cfg.data.monthly_data_cost_usd; prefix: "$"; decimals: 2; step: 1; from: 0; to: 1000; onCommitted: (v) => page.set("data.monthly_data_cost_usd", v) } }
            SettingRow {
                visible: app.mode === "demo"
                label: "demo speed"; help: "how fast the simulated market runs."
                Segmented { small: true; options: [{ value: 1, label: "1×" }, { value: 5, label: "5×" }, { value: 20, label: "20×" }]; current: page.cfg.demo.speed; onPicked: (v) => page.set("demo.speed", v) }
            }
        }

        // ================================================================ about
        Card {
            Layout.fillWidth: true
            title: "about & diagnostics"
            icon: "folder"
            Txt { text: app.appName + " · version " + app.version + " · for personal use only"; color: Theme.textDim; font.pixelSize: 13 }
            Txt {
                text: "this software can lose money. it trades only while this computer is on, awake and online; if it sleeps or loses internet, broker-side stop orders (where accepted) keep protecting open positions until the end of the day. stop orders and loss limits reduce risk but can't guarantee a maximum loss. nothing here is financial advice."
                color: Theme.textMuted; font.pixelSize: 12; wrapMode: Text.WordWrap; Layout.fillWidth: true
            }
            RowLayout {
                DkButton { compact: true; kind: "secondary"; iconName: "folder"; text: "open data folder"; onClicked: app.openDataFolder() }
                DkButton { compact: true; kind: "secondary"; iconName: "folder"; text: "open logs"; onClicked: app.openLogsFolder() }
            }
        }
    }

    DkDialog {
        id: switchDialog
        title: "open positions in this mode"
        message: "switching modes stops the app from managing them. they keep any broker-side stop orders until today's close, and the app picks them back up when you switch back."
        RowLayout {
            Layout.fillWidth: true
            DkButton { text: "cancel"; kind: "ghost"; onClicked: switchDialog.close() }
            Item { Layout.fillWidth: true }
            DkButton { text: "switch anyway"; kind: "primary"; onClicked: { switchDialog.close(); var e = app.setMode(page.pendingMode, true); page.modeMsg = e } }
        }
    }
}
