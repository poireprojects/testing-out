import QtQuick

// Pill-shaped segmented control. options: [{value, label}]
Rectangle {
    id: root
    property var options: []
    property var current
    property bool small: false
    signal picked(var value)
    implicitHeight: small ? 32 : 38
    implicitWidth: row.implicitWidth + 8
    radius: height / 2
    color: Theme.bg
    border.width: 1
    border.color: Theme.border

    Rectangle {
        id: thumb
        property Item target: row.children.length > 0 ? row.children[Math.max(0, root.indexOf(root.current))] : null
        visible: root.indexOf(root.current) >= 0
        x: target ? row.x + target.x : 0
        width: target ? target.width : 0
        y: 4
        height: root.height - 8
        radius: height / 2
        color: Theme.lime
        Behavior on x { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
        Behavior on width { NumberAnimation { duration: 220; easing.type: Easing.OutCubic } }
    }
    function indexOf(v) {
        for (var i = 0; i < options.length; i++) if (options[i].value === v) return i
        return -1
    }
    Row {
        id: row
        x: 4
        anchors.verticalCenter: parent.verticalCenter
        Repeater {
            model: root.options
            delegate: Item {
                width: label.implicitWidth + (root.small ? 24 : 32)
                height: root.height - 8
                Txt {
                    id: label
                    anchors.centerIn: parent
                    text: modelData.label
                    font.pixelSize: root.small ? 12 : 13
                    font.weight: Font.DemiBold
                    color: modelData.value === root.current ? Theme.limeInk : (ma.containsMouse ? Theme.text : Theme.textDim)
                    Behavior on color { ColorAnimation { duration: 160 } }
                }
                MouseArea {
                    id: ma
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (modelData.value !== root.current) root.picked(modelData.value)
                }
            }
        }
    }
}
