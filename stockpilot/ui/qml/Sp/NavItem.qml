import QtQuick

Item {
    id: root
    property string label: ""
    property string iconName: ""
    property bool active: false
    property string badge: ""
    signal clicked()
    implicitHeight: 42
    implicitWidth: 200

    Rectangle {
        anchors.fill: parent
        radius: 12
        color: root.active ? Theme.surface2 : (ma.containsMouse ? Qt.rgba(1, 1, 1, 0.03) : "transparent")
        Behavior on color { ColorAnimation { duration: 140 } }
    }
    Rectangle {
        width: 3
        height: root.active ? 18 : 0
        radius: 2
        color: Theme.lime
        anchors.verticalCenter: parent.verticalCenter
        x: 0
        Behavior on height { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
    }
    Row {
        anchors.verticalCenter: parent.verticalCenter
        x: 16
        spacing: 12
        Icon {
            name: root.iconName
            size: 18
            color: root.active ? Theme.lime : (ma.containsMouse ? Theme.text : Theme.textDim)
            anchors.verticalCenter: parent.verticalCenter
        }
        Txt {
            text: root.label
            font.pixelSize: 14
            font.weight: root.active ? Font.DemiBold : Font.Medium
            color: root.active ? Theme.text : (ma.containsMouse ? Theme.text : Theme.textDim)
            anchors.verticalCenter: parent.verticalCenter
        }
    }
    Pill {
        visible: root.badge !== ""
        text: root.badge
        kind: "lime"
        fontSize: 10
        anchors.right: parent.right
        anchors.rightMargin: 10
        anchors.verticalCenter: parent.verticalCenter
    }
    MouseArea {
        id: ma
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: root.clicked()
    }
}
