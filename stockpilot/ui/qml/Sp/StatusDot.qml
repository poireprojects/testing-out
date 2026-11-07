import QtQuick

Item {
    id: root
    property color color: Theme.lime
    property bool pulse: true
    property real size: 8
    implicitWidth: size * 2.4
    implicitHeight: size * 2.4
    Rectangle {
        id: halo
        anchors.centerIn: parent
        width: root.size; height: root.size
        radius: width / 2
        color: root.color
        opacity: 0
        SequentialAnimation on scale {
            running: root.pulse && root.visible
            loops: Animation.Infinite
            NumberAnimation { from: 1; to: 2.6; duration: 1400; easing.type: Easing.OutCubic }
            PauseAnimation { duration: 400 }
        }
        SequentialAnimation on opacity {
            running: root.pulse && root.visible
            loops: Animation.Infinite
            NumberAnimation { from: 0.5; to: 0; duration: 1400; easing.type: Easing.OutCubic }
            PauseAnimation { duration: 400 }
        }
    }
    Rectangle {
        anchors.centerIn: parent
        width: root.size; height: root.size
        radius: width / 2
        color: root.color
        Behavior on color { ColorAnimation { duration: 250 } }
    }
}
