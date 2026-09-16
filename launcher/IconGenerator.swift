import AppKit
import Foundation

private struct IconVariant {
    let filename: String
    let pixels: Int
}

private let variants = [
    IconVariant(filename: "icon_16x16.png", pixels: 16),
    IconVariant(filename: "icon_16x16@2x.png", pixels: 32),
    IconVariant(filename: "icon_32x32.png", pixels: 32),
    IconVariant(filename: "icon_32x32@2x.png", pixels: 64),
    IconVariant(filename: "icon_128x128.png", pixels: 128),
    IconVariant(filename: "icon_128x128@2x.png", pixels: 256),
    IconVariant(filename: "icon_256x256.png", pixels: 256),
    IconVariant(filename: "icon_256x256@2x.png", pixels: 512),
    IconVariant(filename: "icon_512x512.png", pixels: 512),
    IconVariant(filename: "icon_512x512@2x.png", pixels: 1024),
]

private func drawIcon(pixels: Int) throws -> Data {
    guard let bitmap = NSBitmapImageRep(
        bitmapDataPlanes: nil,
        pixelsWide: pixels,
        pixelsHigh: pixels,
        bitsPerSample: 8,
        samplesPerPixel: 4,
        hasAlpha: true,
        isPlanar: false,
        colorSpaceName: .deviceRGB,
        bytesPerRow: 0,
        bitsPerPixel: 0
    ) else {
        throw NSError(domain: "LocalFlowIcon", code: 1)
    }

    bitmap.size = NSSize(width: pixels, height: pixels)
    guard let context = NSGraphicsContext(bitmapImageRep: bitmap) else {
        throw NSError(domain: "LocalFlowIcon", code: 2)
    }

    NSGraphicsContext.saveGraphicsState()
    NSGraphicsContext.current = context
    let size = CGFloat(pixels)
    let canvas = NSRect(x: 0, y: 0, width: size, height: size)
    NSColor.clear.setFill()
    canvas.fill()

    let tile = canvas.insetBy(dx: size * 0.055, dy: size * 0.055)
    let tilePath = NSBezierPath(
        roundedRect: tile,
        xRadius: size * 0.23,
        yRadius: size * 0.23
    )
    let gradient = NSGradient(colors: [
        NSColor(calibratedRed: 0.08, green: 0.72, blue: 0.95, alpha: 1),
        NSColor(calibratedRed: 0.31, green: 0.27, blue: 0.91, alpha: 1),
    ])
    gradient?.draw(in: tilePath, angle: -58)

    NSGraphicsContext.saveGraphicsState()
    let shadow = NSShadow()
    shadow.shadowColor = NSColor.black.withAlphaComponent(0.24)
    shadow.shadowBlurRadius = size * 0.035
    shadow.shadowOffset = NSSize(width: 0, height: -size * 0.018)
    shadow.set()
    NSColor.white.setFill()
    NSColor.white.setStroke()

    let capsule = NSRect(
        x: size * 0.385,
        y: size * 0.39,
        width: size * 0.23,
        height: size * 0.36
    )
    NSBezierPath(
        roundedRect: capsule,
        xRadius: size * 0.115,
        yRadius: size * 0.115
    ).fill()

    let cradle = NSBezierPath()
    cradle.lineWidth = max(2, size * 0.055)
    cradle.lineCapStyle = .round
    cradle.move(to: NSPoint(x: size * 0.31, y: size * 0.53))
    cradle.curve(
        to: NSPoint(x: size * 0.69, y: size * 0.53),
        controlPoint1: NSPoint(x: size * 0.31, y: size * 0.25),
        controlPoint2: NSPoint(x: size * 0.69, y: size * 0.25)
    )
    cradle.stroke()

    let stem = NSBezierPath()
    stem.lineWidth = max(2, size * 0.055)
    stem.lineCapStyle = .round
    stem.move(to: NSPoint(x: size * 0.50, y: size * 0.30))
    stem.line(to: NSPoint(x: size * 0.50, y: size * 0.20))
    stem.move(to: NSPoint(x: size * 0.40, y: size * 0.20))
    stem.line(to: NSPoint(x: size * 0.60, y: size * 0.20))
    stem.stroke()
    NSGraphicsContext.restoreGraphicsState()
    NSGraphicsContext.restoreGraphicsState()

    guard let data = bitmap.representation(using: .png, properties: [:]) else {
        throw NSError(domain: "LocalFlowIcon", code: 3)
    }
    return data
}

@main
private struct IconGeneratorMain {
    static func main() throws {
        guard CommandLine.arguments.count == 2 else {
            FileHandle.standardError.write(
                Data("usage: IconGenerator OUTPUT.iconset\n".utf8)
            )
            exit(64)
        }
        let destination = URL(fileURLWithPath: CommandLine.arguments[1])
        try FileManager.default.createDirectory(
            at: destination,
            withIntermediateDirectories: true
        )
        for variant in variants {
            let data = try drawIcon(pixels: variant.pixels)
            try data.write(
                to: destination.appendingPathComponent(variant.filename),
                options: .atomic
            )
        }
    }
}

