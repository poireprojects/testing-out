import QtQuick
import QtQuick.Controls.Basic

Switch {
    id: root
    hoverEnabled: true
    implicitWidth: 46
    implicitHeight: 26
    padding: 0
    indicator: Rectangle {
        width: 46; height: 26
        radius: 13
        color: root.checked ? Theme.lime : Theme.surface3
        border.width: 1
        border.color: root.checked ? Theme.lime : (root.hovered ? Theme.borderStrong : Theme.border)
        Behavior on color { ColorAnimation { duration: 160 } }
        Rectangle {
            width: 20; height: 20
            radius: 10
            y: 3
            x: root.checked ? parent.width - width - 3 : 3
            color: root.checked ? Theme.limeInk : Theme.textDim
            Behavior on x { NumberAnimation { duration: 180; easing.type: Easing.OutBack; easing.overshoot: 1.6 } }
            Behavior on color { ColorAnimation { duration: 160 } }
        }
    }
    contentItem: Item {}
}
