import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import QtQuick.Window
import Dk 1.0
import "pages"

ApplicationWindow {
    id: root
    title: app.appName
    color: Theme.bg
    minimumWidth: 1060
    minimumHeight: 680
    width: initialGeometry.width
    height: initialGeometry.height
    x: initialGeometry.x
    y: initialGeometry.y
    visible: false
    font.family: Theme.body

    property int page: Math.max(0, Math.min(5, initialGeometry.page))
    readonly property var st: app.state
    readonly property var cfg: app.settings
    readonly property bool isDemo: st.isDemo === true || app.mode === "demo"
    property bool hiddenHintShown: false

    readonly property var pageInfo: [
        { title: "overview", icon: "overview", sub: "how the account is doing and what the robot is up to." },
        { title: "live scanner", icon: "scanner", sub: "stocks being watched right now, ranked by how interesting they look." },
        { title: "ai activity", icon: "ai", sub: "what the AI is thinking, and why it said yes or no." },
        { title: "trades", icon: "trades", sub: "positions, orders and every completed trade." },
        { title: "performance", icon: "performance", sub: "profits, losses and whether any of this is actually working." },
        { title: "settings", icon: "settings", sub: "brokerage, AI, safety rules and how the app behaves." }
    ]

    Component.onCompleted: {
        if (initialGeometry.maximized) root.visibility = Window.Maximized
        if (!startMinimized) { root.show(); if (initialGeometry.maximized) root.showMaximized() }
    }

    // ---- remember size/position (debounced)
    Timer { id: saveGeo; interval: 700; onTriggered: root.persistGeometry() }
    onXChanged: saveGeo.restart()
    onYChanged: saveGeo.restart()
    onWidthChanged: saveGeo.restart()
    onHeightChanged: saveGeo.restart()
    onPageChanged: saveGeo.restart()
    function persistGeometry() {
        var maxed = root.visibility === Window.Maximized
        app.saveWindow(root.x, root.y, root.width, root.height, maxed, root.page)
    }

    onClosing: function(close) {
        root.persistGeometry()
        close.accepted = false
        if (trayAvailable && cfg.automation && cfg.automation.minimize_to_tray) {
            root.hide()
            if (!hiddenHintShown) hiddenHintShown = true
        } else {
            app.requestQuit()
        }
    }

    Connections {
        target: app
        function onToast(kind, title, message) { toasts.show(kind, title, message) }
        function onQuitRequested() { quitDialog.open() }
    }

    Shortcut { sequences: ["Ctrl+1"]; onActivated: root.page = 0 }
    Shortcut { sequences: ["Ctrl+2"]; onActivated: root.page = 1 }
    Shortcut { sequences: ["Ctrl+3"]; onActivated: root.page = 2 }
    Shortcut { sequences: ["Ctrl+4"]; onActivated: root.page = 3 }
    Shortcut { sequences: ["Ctrl+5"]; onActivated: root.page = 4 }
    Shortcut { sequences: ["Ctrl+,"]; onActivated: root.page = 5 }

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // ================================================================ sidebar
        Rectangle {
            Layout.fillHeight: true
            Layout.preferredWidth: 252
            color: Theme.sidebar
            Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.border }

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: 18
                anchors.topMargin: 22
                spacing: 6

                // brand
                RowLayout {
                    spacing: 12
                    Layout.bottomMargin: 10
                    Image {
                        source: iconUrl
                        sourceSize: Qt.size(84, 84)
                        Layout.preferredWidth: 42
                        Layout.preferredHeight: 42
                        smooth: true
                        mipmap: true
                    }
                    ColumnLayout {
                        spacing: 0
                        Txt { text: "don’t know"; font.family: Theme.display; font.pixelSize: 16; font.weight: Font.Bold; Layout.preferredHeight: 19 }
                        Txt { text: "what im doing"; font.family: Theme.display; font.pixelSize: 16; font.weight: Font.Bold; color: Theme.lime; Layout.preferredHeight: 19 }
                    }
                }
                Txt {
                    text: "a cautious little trading robot"
                    color: Theme.textMuted
                    font.pixelSize: 12
                    Layout.bottomMargin: 12
                }

                // mode badge
                Rectangle {
                    Layout.fillWidth: true
                    Layout.bottomMargin: 14
                    implicitHeight: 52
                    radius: 14
                    color: app.mode === "live" ? Theme.coralSoft : app.mode === "paper" ? Theme.skySoft : Theme.amberSoft
                    border.color: Theme.modeColor(app.mode)
                    border.width: app.mode === "live" ? 1 : 0
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 12
                        spacing: 10
                        StatusDot { color: Theme.modeColor(app.mode); size: 7; pulse: app.mode === "live" }
                        ColumnLayout {
                            spacing: 1
                            Txt {
                                text: app.mode === "live" ? "LIVE · real money" : app.mode === "paper" ? "PAPER · practice money" : "DEMO · simulated"
                                font.pixelSize: 12; font.weight: Font.Bold; font.letterSpacing: 0.5
                                color: Theme.modeColor(app.mode)
                            }
                            Txt {
                                text: app.mode === "live" ? "every trade uses your real account" : app.mode === "paper" ? "real prices, fake money" : "fake prices, fake money"
                                font.pixelSize: 11; color: Theme.textDim
                            }
                        }
                        Item { Layout.fillWidth: true }
                        InfoTip { term: app.mode === "live" ? "live trading" : app.mode === "paper" ? "paper trading" : "demo" }
                    }
                }

                Repeater {
                    model: root.pageInfo
                    delegate: NavItem {
                        Layout.fillWidth: true
                        label: modelData.title
                        iconName: modelData.icon
                        active: root.page === index
                        badge: index === 3 && root.st.positions && root.st.positions.length > 0 ? String(root.st.positions.length) : ""
                        onClicked: root.page = index
                    }
                }

                Item { Layout.fillHeight: true }

                // health
                SectionLabel { text: "system health"; Layout.leftMargin: 4; Layout.bottomMargin: 2 }
                Repeater {
                    model: [
                        { key: "broker", label: "brokerage", icon: "plug" },
                        { key: "data", label: "market data", icon: "data" },
                        { key: "ai", label: "ai", icon: "ai" },
                        { key: "risk", label: "safety rules", icon: "shield" }
                    ]
                    delegate: RowLayout {
                        Layout.fillWidth: true
                        Layout.leftMargin: 4
                        spacing: 8
                        readonly property var h: root.st.health ? root.st.health[modelData.key] : undefined
                        StatusDot { size: 6; pulse: false; color: Theme.healthColor(h ? h.ok : null) }
                        Txt { text: modelData.label; font.pixelSize: 13; color: Theme.textDim }
                        Item { Layout.fillWidth: true }
                        Txt {
                            text: h ? (h.ok === true ? "ok" : h.ok === false ? "issue" : "…") : "—"
                            font.pixelSize: 12
                            color: Theme.healthColor(h ? h.ok : null)
                        }
                        HoverHandler { id: hh }
                        ToolTip.visible: hh.hovered && h !== undefined
                        ToolTip.text: h ? h.message : ""
                        ToolTip.delay: 250
                    }
                }

                // emergency stop
                DkButton {
                    Layout.fillWidth: true
                    Layout.topMargin: 14
                    implicitHeight: 46
                    kind: root.st.flags && root.st.flags.emergencyStop ? "secondary" : "dangerSolid"
                    iconName: root.st.flags && root.st.flags.emergencyStop ? "refresh" : "stop"
                    text: root.st.flags && root.st.flags.emergencyStop ? "reset emergency stop" : "emergency stop"
                    onClicked: root.st.flags && root.st.flags.emergencyStop ? app.resetEmergency() : app.emergencyStop()
                    ToolTip.visible: hovered
                    ToolTip.delay: 400
                    ToolTip.text: app.explain("emergency stop")
                }
                Txt { text: "v" + app.version; color: Theme.textMuted; font.pixelSize: 11; Layout.alignment: Qt.AlignHCenter; Layout.topMargin: 6 }
            }
        }

        // ================================================================ content
        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 0

            // demo banner
            Rectangle {
                Layout.fillWidth: true
                visible: root.isDemo
                implicitHeight: 38
                color: Theme.amberSoft
                Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Qt.rgba(1, 0.78, 0.34, 0.25) }
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 28
                    anchors.rightMargin: 28
                    spacing: 10
                    Icon { name: "flask"; size: 16; color: Theme.amber }
                    Txt {
                        Layout.fillWidth: true
                        text: "demo mode — simulated prices and fake money. nothing here is real performance."
                        color: Theme.amber
                        font.pixelSize: 13
                        font.weight: Font.Medium
                    }
                    Txt { text: root.st.clockText ? "sim time " + root.st.clockText : ""; color: Theme.amber; font.pixelSize: 12; opacity: 0.8 }
                    DkButton { compact: true; kind: "ghost"; text: "connect alpaca →"; onClicked: root.page = 5 }
                }
            }

            // top bar
            Item {
                Layout.fillWidth: true
                implicitHeight: 92
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 28
                    anchors.rightMargin: 28
                    spacing: 16
                    ColumnLayout {
                        spacing: 2
                        Layout.fillWidth: true
                        Txt {
                            text: root.pageInfo[root.page].title
                            font.family: Theme.display
                            font.pixelSize: 30
                            font.weight: Font.Medium
                            font.letterSpacing: -0.5
                        }
                        Txt { text: root.pageInfo[root.page].sub; color: Theme.textMuted; font.pixelSize: 13 }
                    }
                    Rectangle {
                        implicitHeight: 38
                        implicitWidth: sessRow.implicitWidth + 26
                        radius: 19
                        color: Theme.surface
                        border.color: Theme.border
                        RowLayout {
                            id: sessRow
                            anchors.centerIn: parent
                            spacing: 8
                            StatusDot { size: 7; color: root.st.session && root.st.session.isOpen ? Theme.lime : Theme.textMuted; pulse: root.st.session && root.st.session.isOpen === true }
                            Txt { text: root.st.session && root.st.session.text ? root.st.session.text : "connecting…"; font.pixelSize: 13; color: Theme.textDim }
                        }
                    }
                    Rectangle {
                        implicitHeight: 38
                        implicitWidth: apRow.implicitWidth + 24
                        radius: 19
                        color: Theme.surface
                        border.color: cfg.automation && cfg.automation.autopilot ? Qt.rgba(0.78, 1, 0.24, 0.35) : Theme.border
                        RowLayout {
                            id: apRow
                            anchors.centerIn: parent
                            spacing: 9
                            Icon { name: "bolt"; size: 15; color: cfg.automation && cfg.automation.autopilot ? Theme.lime : Theme.textMuted }
                            Txt { text: "autopilot"; font.pixelSize: 13; font.weight: Font.Medium }
                            InfoTip { term: "autopilot" }
                            DkSwitch {
                                checked: cfg.automation ? cfg.automation.autopilot : false
                                onToggled: app.setAutopilot(checked)
                                scale: 0.85
                            }
                        }
                    }
                }
            }

            // pages
            Item {
                id: host
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                PageSlot { index: 0; OverviewPage { anchors.fill: parent; onNavigate: (p) => root.page = p } }
                PageSlot { index: 1; ScannerPage { anchors.fill: parent } }
                PageSlot { index: 2; ActivityPage { anchors.fill: parent } }
                PageSlot { index: 3; TradesPage { anchors.fill: parent } }
                PageSlot { index: 4; PerformancePage { anchors.fill: parent } }
                PageSlot { index: 5; SettingsPage { anchors.fill: parent } }
            }
        }
    }

    component PageSlot: Item {
        property int index: 0
        readonly property bool current: root.page === index
        anchors.fill: parent
        opacity: current ? 1 : 0
        visible: opacity > 0.01
        enabled: current
        transform: Translate { y: current ? 0 : 12; Behavior on y { NumberAnimation { duration: 260; easing.type: Easing.OutCubic } } }
        Behavior on opacity { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
    }

    Toasts {
        id: toasts
        anchors.top: parent.top
        anchors.right: parent.right
        anchors.topMargin: 132
        anchors.rightMargin: 28
        height: 520
        z: 100
    }

    DkDialog {
        id: quitDialog
        title: "you still have open positions"
        message: "the market is open. if you quit, the app stops managing these trades. positions with a broker stop order stay protected until today's close; the app can't watch app-managed stops while it's closed."
        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 6
            spacing: 10
            DkButton { text: "cancel"; kind: "ghost"; onClicked: quitDialog.close() }
            Item { Layout.fillWidth: true }
            DkButton { text: "quit, keep positions"; kind: "secondary"; onClicked: { quitDialog.close(); app.quitApp(false) } }
            DkButton { text: "sell everything & quit"; kind: "danger"; onClicked: { quitDialog.close(); app.quitApp(true) } }
        }
    }

    // ---- guided first-run setup (also reachable from settings)
    SetupWizard { id: welcome }
    Connections { target: app; function onSetupRequested() { root.show(); welcome.open() } }
    Timer { interval: 600; running: cfg.first_run_done === false; onTriggered: welcome.open() }
}
