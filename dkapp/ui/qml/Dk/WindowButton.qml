import QtQuick

// Minimize / maximize-restore / close for the integrated title bar (46 px wide, like Windows 11).
Item {
    id: root
    property string glyph: "winMin"
    property bool danger: false
    property bool externalHover: false     // Windows: hover/press of the maximize button arrive as non-client events
    property bool externalPress: false
    property bool useExternal: false
    property string label: ""
    signal clicked()
    implicitWidth: 46
    implicitHeight: Theme.titleBarHeight

    readonly property bool hot: useExternal ? externalHover : ma.containsMouse
    readonly property bool down: useExternal ? externalPress : ma.pressed

    Accessible.role: Accessible.Button
    Accessible.name: root.label
    Accessible.onPressAction: root.clicked()

    Rectangle {
        anchors.fill: parent
        color: root.danger ? (root.down ? Qt.darker(Theme.closeHover, 1.15) : Theme.closeHover)
                           : (root.down ? Qt.rgba(1, 1, 1, 0.10) : Qt.rgba(1, 1, 1, 0.065))
        opacity: root.hot || root.down ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 90 } }
    }
    Icon {
        anchors.centerIn: parent
        name: root.glyph
        size: 16
        stroke: 1.1
        color: root.danger && (root.hot || root.down) ? "#FFFFFF" : (root.hot ? Theme.text : Theme.textDim)
    }
    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        enabled: !root.useExternal
        acceptedButtons: Qt.LeftButton
        onClicked: root.clicked()
    }
}
