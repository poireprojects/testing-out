import QtQuick
import QtQuick.Controls.Basic

TextField {
    id: root
    property bool secret: false
    hoverEnabled: true
    implicitHeight: 40
    leftPadding: 14
    rightPadding: 14
    color: Theme.text
    placeholderTextColor: Theme.textMuted
    selectionColor: Theme.limeGlow
    selectedTextColor: Theme.text
    font.family: Theme.body
    font.pixelSize: 14
    echoMode: secret ? TextInput.Password : TextInput.Normal
    passwordCharacter: "•"
    background: Rectangle {
        radius: 11
        color: Theme.bg
        border.width: 1
        border.color: root.activeFocus ? Theme.lime : (root.hovered ? Theme.borderStrong : Theme.border)
        Behavior on border.color { ColorAnimation { duration: 140 } }
    }
}
