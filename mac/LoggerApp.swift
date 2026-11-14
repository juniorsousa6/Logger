// Logger.app — janela nativa do Mac para o Logger.
// Sobe o motor em Python (app.py) em segundo plano, mostra a interface num WKWebView
// e cuida de: escolher pastas (painel nativo), não fechar no meio de uma cópia,
// atualização automática e recompilar sozinho quando o macOS muda de versão.
// Compilado no próprio Mac pelo instalador (swiftc das Ferramentas de Linha de Comando).

import Cocoa
import CryptoKit
import WebKit

let info = Bundle.main.infoDictionary ?? [:]
let APP_VERSION = info["CFBundleShortVersionString"] as? String ?? "0"
let UPDATE_URL = (info["LoggerUpdateURL"] as? String ?? "").trimmingCharacters(in: .whitespaces)
let BUILT_ON = info["LoggerBuiltOn"] as? String ?? ""
let SUPPORT = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Application Support/Logger")
let LOG_FILE = FileManager.default.homeDirectoryForCurrentUser
    .appendingPathComponent("Library/Logs/Logger.log")

final class TextBox { var text = ""; var found = false }

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKScriptMessageHandlerWithReply {
    var window: NSWindow!
    var web: WKWebView!
    var server: Process?
    var baseURL: URL?
    var quitting = false

    // MARK: ciclo de vida
    func applicationDidFinishLaunching(_ n: Notification) {
        buildMenu()
        let cfg = WKWebViewConfiguration()
        cfg.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "logger")
        web = WKWebView(frame: .zero, configuration: cfg)
        web.setValue(false, forKey: "drawsBackground")
        web.navigationDelegate = self

        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1100, height: 820),
                          styleMask: [.titled, .closable, .miniaturizable, .resizable],
                          backing: .buffered, defer: false)
        window.title = "Logger"
        window.titlebarAppearsTransparent = true
        window.titleVisibility = .hidden
        window.appearance = NSAppearance(named: .darkAqua)
        window.backgroundColor = NSColor(srgbRed: 0.027, green: 0.031, blue: 0.039, alpha: 1)
        window.minSize = NSSize(width: 720, height: 560)
        window.center()
        window.setFrameAutosaveName("LoggerMain")
        window.contentView = web
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)

        guard toolsInstalled() else { askInstallTools(); return }
        startServer()
        if osChangedSinceBuild() { offerRebuild() }
        else { DispatchQueue.main.asyncAfter(deadline: .now() + 4) { self.checkUpdates(manual: false) } }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ s: NSApplication) -> Bool { true }

    func applicationShouldTerminate(_ s: NSApplication) -> NSApplication.TerminateReply {
        if !quitting && copyRunning() {
            let a = NSAlert()
            a.messageText = "Tem uma cópia em andamento"
            a.informativeText = "Se sair agora, a cópia para no meio. Nada é apagado do cartão, e da próxima vez o Logger copia só o que faltou."
            a.addButton(withTitle: "Continuar copiando")
            a.addButton(withTitle: "Sair mesmo assim")
            if a.runModal() == .alertFirstButtonReturn { return .terminateCancel }
        }
        quitting = true
        return .terminateNow
    }

    func applicationWillTerminate(_ n: Notification) {
        quitting = true
        server?.terminate()
    }

    // MARK: motor em Python
    func toolsInstalled() -> Bool {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/xcode-select")
        p.arguments = ["-p"]
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        do { try p.run(); p.waitUntilExit() } catch { return false }
        return p.terminationStatus == 0
    }

    func askInstallTools() {
        let a = NSAlert()
        a.messageText = "Falta um componente grátis da Apple"
        a.informativeText = "O Logger usa as Ferramentas de Linha de Comando da Apple. Clique em Instalar, espere terminar e abra o Logger de novo."
        a.addButton(withTitle: "Instalar")
        a.addButton(withTitle: "Sair")
        if a.runModal() == .alertFirstButtonReturn {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: "/usr/bin/xcode-select")
            p.arguments = ["--install"]
            try? p.run()
        }
        NSApp.terminate(nil)
    }

    func startServer() {
        guard let res = Bundle.main.resourceURL?.appendingPathComponent("app") else { return }
        try? FileManager.default.createDirectory(at: LOG_FILE.deletingLastPathComponent(), withIntermediateDirectories: true)
        if !FileManager.default.fileExists(atPath: LOG_FILE.path) {
            FileManager.default.createFile(atPath: LOG_FILE.path, contents: nil)
        }
        let err = try? FileHandle(forWritingTo: LOG_FILE)
        err?.seekToEndOfFile()

        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        p.arguments = [res.appendingPathComponent("app.py").path]
        p.currentDirectoryURL = res
        var env = ProcessInfo.processInfo.environment
        env["LOGGER_PORT"] = "0"
        env["LOGGER_NO_BROWSER"] = "1"
        env["LOGGER_PARENT_PID"] = String(ProcessInfo.processInfo.processIdentifier)
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONUNBUFFERED"] = "1"
        p.environment = env
        let out = Pipe()
        p.standardOutput = out
        p.standardError = err ?? FileHandle.nullDevice
        let buffer = TextBox()
        out.fileHandleForReading.readabilityHandler = { [weak self] h in
            let d = h.availableData
            if d.isEmpty { h.readabilityHandler = nil; return }
            buffer.text += String(decoding: d, as: UTF8.self)
            guard !buffer.found, let r = buffer.text.range(of: "LOGGER_URL "),
                  buffer.text[r.upperBound...].contains("\n") else { return }
            buffer.found = true
            let line = buffer.text[r.upperBound...].prefix { $0 != "\n" }.trimmingCharacters(in: .whitespaces)
            guard let u = URL(string: line) else { return }
            DispatchQueue.main.async {
                self?.baseURL = u
                self?.web.load(URLRequest(url: u))
            }
        }
        p.terminationHandler = { [weak self] pr in
            DispatchQueue.main.async {
                guard let self = self, !self.quitting else { return }
                self.serverDied(pr.terminationStatus)
            }
        }
        do { try p.run(); server = p } catch { serverDied(-1) }
    }

    func serverDied(_ code: Int32) {
        let a = NSAlert()
        a.messageText = "O motor do Logger parou"
        a.informativeText = "Código \(code). O registro está em ~/Library/Logs/Logger.log. Pode reabrir; nada no cartão foi alterado."
        a.addButton(withTitle: "Reabrir")
        a.addButton(withTitle: "Mostrar registro")
        a.addButton(withTitle: "Sair")
        switch a.runModal() {
        case .alertFirstButtonReturn: baseURL = nil; startServer()
        case .alertSecondButtonReturn: NSWorkspace.shared.activateFileViewerSelecting([LOG_FILE]); NSApp.terminate(nil)
        default: NSApp.terminate(nil)
        }
    }

    func copyRunning() -> Bool {
        guard let b = baseURL, let u = URL(string: "api/progress", relativeTo: b),
              let d = try? Data(contentsOf: u),
              let j = try? JSONSerialization.jsonObject(with: d) as? [String: Any],
              let phase = j["phase"] as? String else { return false }
        return ["comecando", "copiando", "conferindo", "relatorio"].contains(phase)
    }

    // MARK: ponte com a interface (painel nativo de pastas)
    func userContentController(_ c: WKUserContentController, didReceive m: WKScriptMessage,
                               replyHandler: @escaping (Any?, String?) -> Void) {
        guard let body = m.body as? [String: Any], body["cmd"] as? String == "pick" else {
            replyHandler(nil, "comando desconhecido"); return
        }
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.message = body["prompt"] as? String ?? "Escolha a pasta"
        panel.prompt = "Escolher"
        panel.directoryURL = URL(fileURLWithPath: "/Volumes")
        panel.beginSheetModal(for: window) { r in
            replyHandler(r == .OK ? panel.url?.path : "", nil)
        }
    }

    // links externos abrem no navegador, não dentro do app
    func webView(_ w: WKWebView, decidePolicyFor a: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if let u = a.request.url, let host = u.host, host != "127.0.0.1", u.scheme?.hasPrefix("http") == true {
            NSWorkspace.shared.open(u); decisionHandler(.cancel); return
        }
        decisionHandler(.allow)
    }

    // MARK: atualizações
    func newer(_ a: String, than b: String) -> Bool {
        let x = a.split(separator: ".").map { Int($0) ?? 0 }, y = b.split(separator: ".").map { Int($0) ?? 0 }
        for i in 0..<max(x.count, y.count) {
            let p = i < x.count ? x[i] : 0, q = i < y.count ? y[i] : 0
            if p != q { return p > q }
        }
        return false
    }

    @objc func checkUpdatesMenu(_ s: Any?) { checkUpdates(manual: true) }

    func checkUpdates(manual: Bool) {
        guard !UPDATE_URL.isEmpty, let u = URL(string: UPDATE_URL) else {
            if manual { say("Atualização automática ainda não configurada", "Por enquanto, rode o instalador da versão nova. Suas configurações e o histórico ficam.") }
            return
        }
        var req = URLRequest(url: u)
        req.cachePolicy = .reloadIgnoringLocalCacheData
        URLSession.shared.dataTask(with: req) { d, _, _ in
            let j = d.flatMap { try? JSONSerialization.jsonObject(with: $0) as? [String: Any] }
            DispatchQueue.main.async {
                guard let j = j, let v = j["version"] as? String, let zip = j["url"] as? String,
                      let sha = j["sha256"] as? String, !sha.isEmpty else {
                    if manual { self.say("Não consegui verificar agora", "Confira a internet e tente de novo.") }
                    return
                }
                if self.newer(v, than: APP_VERSION) {
                    if !manual && self.copyRunning() { return }  // não interrompe cópia; avisa na próxima abertura
                    self.offerUpdate(v, zip: zip, sha: sha, notes: j["notes"] as? String ?? "")
                } else if manual {
                    self.say("Você já está na versão mais nova", "Logger \(APP_VERSION).")
                }
            }
        }.resume()
    }

    func offerUpdate(_ v: String, zip: String, sha: String, notes: String) {
        let a = NSAlert()
        a.messageText = "Logger \(v) disponível"
        a.informativeText = (notes.isEmpty ? "" : notes + "\n\n") + "Você está na \(APP_VERSION). A atualização leva menos de um minuto e mantém suas pastas, cartões lembrados e histórico."
        a.addButton(withTitle: "Atualizar agora")
        a.addButton(withTitle: "Depois")
        guard a.runModal() == .alertFirstButtonReturn, let u = URL(string: zip) else { return }
        URLSession.shared.downloadTask(with: u) { tmp, _, err in
            guard let tmp = tmp, err == nil else {
                DispatchQueue.main.async { self.say("O download falhou", err?.localizedDescription ?? "") }
                return
            }
            let dir = FileManager.default.temporaryDirectory.appendingPathComponent("logger-update-\(v)")
            try? FileManager.default.removeItem(at: dir)
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            let z = dir.appendingPathComponent("pacote.zip")
            try? FileManager.default.moveItem(at: tmp, to: z)
            // so instala se o pacote baixado for exatamente o publicado (impressao digital SHA-256)
            let got = (try? Data(contentsOf: z)).map { SHA256.hash(data: $0).map { String(format: "%02x", $0) }.joined() } ?? ""
            guard got == sha.lowercased() else {
                DispatchQueue.main.async { self.say("O pacote baixado não confere", "A atualização foi cancelada por segurança. Tente de novo mais tarde.") }
                return
            }
            let unzip = Process()
            unzip.executableURL = URL(fileURLWithPath: "/usr/bin/ditto")
            unzip.arguments = ["-x", "-k", z.path, dir.path]
            try? unzip.run(); unzip.waitUntilExit()
            let installer = dir.appendingPathComponent("Logger/Instalar Logger.command")
            DispatchQueue.main.async { self.runInstaller(installer) }
        }.resume()
    }

    // MARK: recompilar quando o macOS muda
    func osChangedSinceBuild() -> Bool {
        let now = ProcessInfo.processInfo.operatingSystemVersion.majorVersion
        guard let built = Int(BUILT_ON.split(separator: ".").first ?? "") else { return false }
        return built != now
    }

    func offerRebuild() {
        let installer = SUPPORT.appendingPathComponent("source/Instalar Logger.command")
        guard FileManager.default.fileExists(atPath: installer.path) else { return }
        let a = NSAlert()
        a.messageText = "O macOS foi atualizado"
        a.informativeText = "O Logger foi preparado para o macOS \(BUILT_ON). Quer refazer o app para esta versão do sistema? Leva menos de um minuto e mantém tudo."
        a.addButton(withTitle: "Atualizar o Logger")
        a.addButton(withTitle: "Agora não")
        if a.runModal() == .alertFirstButtonReturn { runInstaller(installer) }
    }

    func runInstaller(_ installer: URL) {
        if copyRunning() {  // nunca fecha o app no meio de uma copia
            say("Tem uma cópia em andamento", "Espere a cópia terminar e atualize depois (menu Logger › Procurar Atualizações…).")
            return
        }
        guard FileManager.default.fileExists(atPath: installer.path) else {
            say("Pacote de atualização incompleto", "Tente de novo mais tarde."); return
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/bin/bash")
        p.arguments = [installer.path, "--update", "--wait-pid", String(ProcessInfo.processInfo.processIdentifier)]
        let log = try? FileHandle(forWritingTo: LOG_FILE)
        log?.seekToEndOfFile()
        p.standardOutput = log ?? FileHandle.nullDevice
        p.standardError = log ?? FileHandle.nullDevice
        do { try p.run() } catch { say("Não consegui atualizar", error.localizedDescription); return }
        quitting = true
        NSApp.terminate(nil)
    }

    // MARK: menus e avisos
    func say(_ t: String, _ m: String) {
        let a = NSAlert(); a.messageText = t; a.informativeText = m; a.runModal()
    }

    @objc func reload(_ s: Any?) { if let b = baseURL { web.load(URLRequest(url: b)) } }
    @objc func showLog(_ s: Any?) { NSWorkspace.shared.activateFileViewerSelecting([LOG_FILE]) }

    func buildMenu() {
        let main = NSMenu()
        func sub(_ title: String, _ items: [NSMenuItem]) {
            let it = NSMenuItem(); let m = NSMenu(title: title)
            items.forEach { m.addItem($0) }
            it.submenu = m; main.addItem(it)
        }
        func item(_ t: String, _ a: Selector?, _ k: String = "", target: AnyObject? = nil, mods: NSEvent.ModifierFlags = [.command]) -> NSMenuItem {
            let i = NSMenuItem(title: t, action: a, keyEquivalent: k)
            i.keyEquivalentModifierMask = mods
            i.target = target
            return i
        }
        sub("Logger", [
            item("Sobre o Logger", #selector(NSApplication.orderFrontStandardAboutPanel(_:))),
            item("Procurar Atualizações…", #selector(checkUpdatesMenu(_:)), target: self),
            .separator(),
            item("Ocultar Logger", #selector(NSApplication.hide(_:)), "h"),
            item("Ocultar Outros", #selector(NSApplication.hideOtherApplications(_:)), "h", mods: [.command, .option]),
            .separator(),
            item("Sair do Logger", #selector(NSApplication.terminate(_:)), "q"),
        ])
        sub("Editar", [
            item("Desfazer", Selector(("undo:")), "z"),
            item("Refazer", Selector(("redo:")), "z", mods: [.command, .shift]),
            .separator(),
            item("Recortar", #selector(NSText.cut(_:)), "x"),
            item("Copiar", #selector(NSText.copy(_:)), "c"),
            item("Colar", #selector(NSText.paste(_:)), "v"),
            item("Selecionar Tudo", #selector(NSText.selectAll(_:)), "a"),
        ])
        sub("Janela", [
            item("Minimizar", #selector(NSWindow.performMiniaturize(_:)), "m"),
            item("Recarregar", #selector(reload(_:)), "r", target: self),
            .separator(),
            item("Mostrar Registro", #selector(showLog(_:)), target: self),
        ])
        NSApp.mainMenu = main
    }
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.regular)
app.run()
