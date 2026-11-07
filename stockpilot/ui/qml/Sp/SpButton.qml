import QtQuick
import QtQuick.Controls.Basic

Button {
    id: root
    property string kind: "secondary"  // primary | secondary | ghost | danger | dangerSolid
    property string iconName: ""
    property bool compact: false
    hoverEnabled: true
    focusPolicy: Qt.StrongFocus
    implicitHeight: compact ? 32 : 40
    leftPadding: compact ? 12 : 16
    rightPadding: compact ? 12 : 16
    font.family: Theme.body
    font.pixelSize: compact ? 13 : 14
    font.weight: Font.DemiBold

    readonly property color fg: kind === "primary" ? Theme.limeInk
                              : kind === "dangerSolid" ? "#1A0705"
                              : kind === "danger" ? Theme.coral
                              : kind === "ghost" ? Theme.textDim : Theme.text
    readonly property color base: kind === "primary" ? Theme.lime
                                : kind === "dangerSolid" ? Theme.coral
                                : kind === "danger" ? Theme.coralSoft
                                : kind === "ghost" ? "transparent" : Theme.surface2

    scale: down ? 0.97 : 1.0
    Behavior on scale { NumberAnimation { duration: 90 } }
    opacity: enabled ? 1.0 : 0.45

    contentItem: Row {
        spacing: 7
        Icon {
            visible: root.iconName !== ""
            name: root.iconName
            size: root.compact ? 14 : 16
            color: root.fg
            stroke: 2.0
            anchors.verticalCenter: parent.verticalCenter
        }
        Txt {
            text: root.text
            color: root.fg
            font: root.font
            anchors.verticalCenter: parent.verticalCenter
        }
    }
    background: Rectangle {
        radius: root.compact ? 9 : 12
        color: root.hovered ? Qt.lighter(root.base, root.kind === "ghost" ? 1.0 : 1.08) : root.base
        border.width: root.kind === "secondary" || root.kind === "ghost" ? 1 : 0
        border.color: root.hovered ? Theme.borderStrong : Theme.border
        Behavior on color { ColorAnimation { duration: 140 } }
        Rectangle {
            anchors.fill: parent
            radius: parent.radius
            color: "transparent"
            border.width: root.visualFocus ? 2 : 0
            border.color: Theme.lime
        }
        Rectangle {  // soft glow for the primary action
            visible: root.kind === "primary" && root.hovered
            anchors.fill: parent
            anchors.margins: -4
            radius: parent.radius + 4
            color: "transparent"
            border.width: 4
            border.color: Theme.limeSoft
        }
    }
}
