import QtQuick
import QtQuick.Layouts

// Integrated window frame: brand, where you are, live status, and the window controls.
// Empty space drags the window (Windows: the OS handles it natively, incl. double-click and snap).
Rectangle {
    id: bar
    property int sidebarWidth: 252
    property string pageTitle: ""
    property var session: ({})
    property string mode: "demo"
    property var quant: ({})
    property bool isTestCopy: false
    implicitHeight: Theme.titleBarHeight
    color: Theme.titleBar

    function report() {
        if (typeof chrome === "undefined" || !chrome.custom) return
        var m = maxBtn.mapToItem(null, 0, 0)
        var a = minBtn.mapToItem(null, 0, 0)
        var c = closeBtn.mapToItem(null, 0, 0)
        chrome.setRegions(bar.height,
                          [m.x, m.y, maxBtn.width, maxBtn.height],
                          [[a.x, a.y, minBtn.width, minBtn.height], [c.x, c.y, closeBtn.width, closeBtn.height]])
    }
    onWidthChanged: regionTimer.restart()
    onHeightChanged: regionTimer.restart()
    Component.onCompleted: regionTimer.restart()
    Timer { id: regionTimer; interval: 30; onTriggered: bar.report() }

    // Linux/other: drag + double-click on empty space (Windows does this natively via hit-testing)
    MouseArea {
        anchors.fill: parent
        enabled: typeof chrome !== "undefined" && chrome.custom && !chrome.nativeHitTest
        acceptedButtons: Qt.LeftButton
        onPressed: chrome.startMove()
        onDoubleClicked: chrome.toggleMaximize()
    }

    Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.border }
    // brand column lines up with the sidebar below it
    Rectangle { x: bar.sidebarWidth - 1; width: 1; height: parent.height; color: Theme.border; opacity: 0.7 }

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 16
        spacing: 0

        // ---- brand (inside the sidebar column)
        RowLayout {
            objectName: "brand"
            Layout.preferredWidth: bar.sidebarWidth - 16
            Layout.maximumWidth: bar.sidebarWidth - 16
            spacing: 10
            Image {
                source: iconUrl
                sourceSize: Qt.size(56, 56)
                Layout.preferredWidth: 24
                Layout.preferredHeight: 24
                smooth: true
                mipmap: true
            }
            Row {
                spacing: 4
                Txt { text: "don’t know"; font.family: Theme.display; font.pixelSize: 14; font.weight: Font.Bold }
                Txt { text: "what im doing"; font.family: Theme.display; font.pixelSize: 14; font.weight: Font.Bold; color: Theme.lime }
            }
            Item { Layout.fillWidth: true }
        }

        // ---- where you are
        Txt {
            Layout.leftMargin: 20
            text: bar.pageTitle
            color: Theme.textDim
            font.pixelSize: 13
            font.weight: Font.Medium
        }
        Item { Layout.fillWidth: true }

        // ---- compact live status (informational: dragging through it still moves the window)
        Rectangle {
            visible: bar.isTestCopy
            Layout.rightMargin: 8
            implicitHeight: 26; implicitWidth: tcTxt.implicitWidth + 20; radius: 13
            color: Theme.skySoft
            Txt { id: tcTxt; anchors.centerIn: parent; text: "test copy"; color: Theme.sky; font.pixelSize: 11; font.weight: Font.DemiBold }
        }
        Row {
            Layout.rightMargin: 14
            spacing: 7
            visible: bar.width > 900
            StatusDot {
                anchors.verticalCenter: parent.verticalCenter
                size: 6
                color: bar.session && bar.session.isOpen ? Theme.lime : Theme.textMuted
                pulse: false
            }
            Txt {
                anchors.verticalCenter: parent.verticalCenter
                text: bar.session && bar.session.text ? bar.session.text : "connecting…"
                color: Theme.textDim; font.pixelSize: 12
                elide: Text.ElideRight
                width: Math.min(implicitWidth, 320)
            }
        }
        Rectangle {
            Layout.rightMargin: 14
            visible: bar.quant && bar.quant.available === true && bar.width > 1120
            implicitHeight: 24; implicitWidth: qRow.implicitWidth + 18; radius: 12
            color: "transparent"; border.color: Theme.borderStrong
            Row {
                id: qRow
                anchors.centerIn: parent
                spacing: 6
                Icon { name: "brain"; size: 13; color: Theme.textDim; anchors.verticalCenter: parent.verticalCenter }
                Txt {
                    text: "model · " + (bar.quant && bar.quant.gating ? "gating paper" : "shadow")
                    color: Theme.textDim; font.pixelSize: 11; anchors.verticalCenter: parent.verticalCenter
                }
            }
        }
        Rectangle {
            Layout.rightMargin: 12
            implicitHeight: 24; implicitWidth: modeTxt.implicitWidth + 18; radius: 12
            color: bar.mode === "live" ? Theme.coralSoft : bar.mode === "paper" ? Theme.skySoft : Theme.amberSoft
            border.color: bar.mode === "live" ? Theme.coral : "transparent"
            Txt {
                id: modeTxt
                anchors.centerIn: parent
                text: bar.mode === "live" ? "LIVE" : bar.mode === "paper" ? "PAPER" : "DEMO"
                color: Theme.modeColor(bar.mode)
                font.pixelSize: 11; font.weight: Font.Bold; font.letterSpacing: 0.8
            }
        }

        // ---- window controls
        Row {
            Layout.fillHeight: true
            visible: typeof chrome !== "undefined" && chrome.custom
            WindowButton { id: minBtn; glyph: "winMin"; label: "minimize"; height: bar.height; onClicked: chrome.minimize() }
            WindowButton {
                id: maxBtn
                height: bar.height
                glyph: typeof chrome !== "undefined" && chrome.maximized ? "winRestore" : "winMax"
                label: typeof chrome !== "undefined" && chrome.maximized ? "restore" : "maximize"
                useExternal: typeof chrome !== "undefined" && chrome.nativeHitTest
                externalHover: typeof chrome !== "undefined" && chrome.maxHovered
                externalPress: typeof chrome !== "undefined" && chrome.maxPressed
                onClicked: chrome.toggleMaximize()
            }
            WindowButton { id: closeBtn; glyph: "winClose"; label: "close"; danger: true; height: bar.height; onClicked: chrome.closeWindow() }
        }
    }
}
