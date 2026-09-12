import AppKit
import Foundation
import PDFKit

guard CommandLine.arguments.count == 4 else {
    fputs("usage: swift render_floorplans.swift SCOTT_DIR WEAN_DIR OUTPUT_DIR\n", stderr)
    exit(2)
}

let manager = FileManager.default
let outputDirectory = URL(fileURLWithPath: CommandLine.arguments[3], isDirectory: true)
try manager.createDirectory(at: outputDirectory, withIntermediateDirectories: true)

for (building, directory) in [("sh", CommandLine.arguments[1]), ("weh", CommandLine.arguments[2])] {
    let directoryURL = URL(fileURLWithPath: directory, isDirectory: true)
    let files = try manager.contentsOfDirectory(at: directoryURL, includingPropertiesForKeys: nil)
        .filter { $0.pathExtension.lowercased() == "pdf" }
        .sorted { $0.lastPathComponent < $1.lastPathComponent }

    for input in files {
        let parts = input.deletingPathExtension().lastPathComponent.split(separator: "-")
        let level = building == "weh" ? String(parts[1]) : parts[1].lowercased()
        let output = outputDirectory.appendingPathComponent("\(building)-\(level).png")
        guard let document = PDFDocument(url: input), let page = document.page(at: 0) else { continue }
        let image = page.thumbnail(of: NSSize(width: 1800, height: 1800), for: .mediaBox)
        guard let tiff = image.tiffRepresentation,
              let bitmap = NSBitmapImageRep(data: tiff),
              let png = bitmap.representation(using: .png, properties: [:]) else { continue }
        try png.write(to: output)
        print(output.lastPathComponent)
        withExtendedLifetime(document) {}
    }
}
