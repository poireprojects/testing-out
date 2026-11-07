import QtQuick

Rectangle {
    id: root
    property real value: 0     // 0..1
    property color barColor: Theme.lime
    implicitHeight: 6
    radius: height / 2
    color: Theme.surface3
    Rectangle {
        height: parent.height
        radius: parent.radius
        width: Math.max(parent.height, parent.width * Math.max(0, Math.min(1, root.value)))
        color: root.barColor
        visible: root.value > 0
        Behavior on width { NumberAnimation { duration: 500; easing.type: Easing.OutCubic } }
        Behavior on color { ColorAnimation { duration: 200 } }
    }
}
