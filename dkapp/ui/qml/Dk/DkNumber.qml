import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic

// Number input with − / + steppers. Emits `committed(value)` when the user changes it.
Rectangle {
    id: root
    property real value: 0
    property real step: 1
    property real from: 0
    property real to: 1e9
    property int decimals: 0
    property string prefix: ""
    property string suffix: ""
    signal committed(real value)

    implicitWidth: 168
    implicitHeight: 38
    radius: 11
    color: Theme.bg
    border.width: 1
    border.color: field.activeFocus ? Theme.lime : Theme.border

    function clamp(v) { return Math.max(from, Math.min(to, v)) }
    function commit(v) {
        var c = clamp(Math.round(v * Math.pow(10, decimals)) / Math.pow(10, decimals))
        if (Math.abs(c - value) > 1e-9) committed(c)
        field.text = Theme.fixed(c, decimals)
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 3
        spacing: 0
        StepButton { label: "−"; onClicked: root.commit(root.value - root.step) }
        Txt { text: root.prefix; color: Theme.textMuted; visible: root.prefix !== ""; Layout.leftMargin: 4 }
        TextInput {
            id: field
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignHCenter
            color: Theme.text
            font.family: Theme.body
            font.pixelSize: 14
            font.features: { "tnum": 1 }
            selectByMouse: true
            selectionColor: Theme.limeGlow
            text: Theme.fixed(root.value, root.decimals)
            validator: DoubleValidator { bottom: root.from; top: root.to; decimals: root.decimals; notation: DoubleValidator.StandardNotation }
            onEditingFinished: root.commit(parseFloat(text.replace(/,/g, "")) || 0)
            Connections { target: root; function onValueChanged() { if (!field.activeFocus) field.text = Theme.fixed(root.value, root.decimals) } }
        }
        Txt { text: root.suffix; color: Theme.textMuted; visible: root.suffix !== ""; Layout.rightMargin: 4 }
        StepButton { label: "+"; onClicked: root.commit(root.value + root.step) }
    }

    component StepButton: AbstractButton {
        property string label
        hoverEnabled: true
        implicitWidth: 30
        implicitHeight: 30
        contentItem: Txt { text: parent.label; horizontalAlignment: Text.AlignHCenter; color: parent.hovered ? Theme.lime : Theme.textDim; font.pixelSize: 16 }
        background: Rectangle { radius: 8; color: parent.hovered ? Theme.surface2 : "transparent" }
    }
}
