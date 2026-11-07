import QtQuick

Text {
    color: Theme.text
    font.family: Theme.body
    font.pixelSize: 14
    font.features: { "tnum": 1 }
    elide: Text.ElideRight
    textFormat: Text.PlainText
    verticalAlignment: Text.AlignVCenter
}
