import QtQuick
import QtQuick.Layouts
import QtQuick.Controls.Basic

Popup {
    id: root
    property string title: ""
    property string message: ""
    default property alias content: body.data
    modal: true
    focus: true
    anchors.centerIn: Overlay.overlay
    width: Math.min(520, parent ? parent.width - 80 : 520)
    padding: 26
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, 0.6) }
    background: Rectangle { radius: 22; color: Theme.surface; border.color: Theme.borderStrong }
    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: 160 }
            NumberAnimation { property: "scale"; from: 0.96; to: 1; duration: 200; easing.type: Easing.OutCubic }
        }
    }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: 120 } }
    contentItem: ColumnLayout {
        spacing: 14
        Txt { text: root.title; font.family: Theme.display; font.pixelSize: 21; font.weight: Font.Medium; visible: root.title !== "" }
        Txt {
            text: root.message; visible: root.message !== ""; color: Theme.textDim; font.pixelSize: 14
            wrapMode: Text.WordWrap; elide: Text.ElideNone; Layout.fillWidth: true; lineHeight: 1.25
        }
        ColumnLayout { id: body; Layout.fillWidth: true; spacing: 12 }
    }
}
