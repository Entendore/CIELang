import sys
import re
import math
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPlainTextEdit, QPushButton, QTextEdit, QSplitter, QTabWidget,
    QGraphicsView, QGraphicsScene, QGraphicsRectItem, QGraphicsTextItem, 
    QGraphicsLineItem, QGraphicsPolygonItem
)
from PySide6.QtGui import QColor, QTextCharFormat, QFont, QSyntaxHighlighter, QPen, QBrush, QPolygonF
from PySide6.QtCore import Qt, QRegularExpression, QTimer, QRectF, QPointF

# ==========================================
# 1. CIELAB MATH & COLOR SCIENCE ENGINE
# ==========================================

def lab_to_rgb(l, a, b):
    fy = (l + 16) / 116.0
    fx = a / 500.0 + fy
    fz = fy - b / 200.0

    def lab_to_xyz_helper(t):
        t3 = t ** 3
        if t3 > 0.008856:
            return t3
        return (t - 16 / 116.0) / 7.787

    xr = lab_to_xyz_helper(fx)
    yr = lab_to_xyz_helper(fy)
    zr = lab_to_xyz_helper(fz)

    x = xr * 95.047
    y = yr * 100.000
    z = zr * 108.883

    r = x * 3.2406 + y * -1.5372 + z * -0.4986
    g = x * -0.9689 + y * 1.8758 + z * 0.0415
    b_val = x * 0.0557 + y * -0.2040 + z * 1.0570

    def gamma_correct(c):
        c = max(0.0, c)  # Prevent undefined behavior for negative inputs
        if c > 0.0031308:
            return 1.055 * (c ** (1 / 2.4)) - 0.055
        return 12.92 * c

    r = gamma_correct(r / 100.0)
    g = gamma_correct(g / 100.0)
    b_val = gamma_correct(b_val / 100.0)

    r = max(0, min(255, int(r * 255)))
    g = max(0, min(255, int(g * 255)))
    b_val = max(0, min(255, int(b_val * 255)))
    return r, g, b_val

def mix_lab(lab1, lab2):
    # Clamped L to [0, 100] to prevent drift on repeated subtraction
    return (
        max(0.0, min(100.0, lab1[0] + lab2[0])),
        max(-128.0, min(127.0, lab1[1] + lab2[1])),
        max(-128.0, min(127.0, lab1[2] + lab2[2]))
    )

# ==========================================
# 2. OPTICA SYNTAX HIGHLIGHTER
# ==========================================

class OpticaHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.rules = []

        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("#C678DD"))
        keyword_format.setFontWeight(QFont.Bold)
        keywords = r'\b(emit|filter|prism|observe|add|subtract|axis|accumulate|delay|pulse|split|splitter|spectrum|latch|poll)\b'
        self.rules.append((QRegularExpression(keywords), keyword_format))

        lab_format = QTextCharFormat()
        lab_format.setForeground(QColor("#D19A66"))
        self.rules.append((QRegularExpression(r'\b[Lab]-?\d+(?:\.\d+)?\b'), lab_format))

        comment_format = QTextCharFormat()
        comment_format.setForeground(QColor("#5C6370"))
        comment_format.setFontItalic(True)
        self.rules.append((QRegularExpression(r'//[^\n]*'), comment_format))

        op_format = QTextCharFormat()
        op_format.setForeground(QColor("#56B6C2"))
        self.rules.append((QRegularExpression(r'->'), op_format))

    def highlightBlock(self, text):
        for pattern, fmt in self.rules:
            matchIterator = pattern.globalMatch(text)
            while matchIterator.hasNext():
                match = matchIterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)

# ==========================================
# 3. TIME-STEPPED OPTICAL NODES
# ==========================================

class OpticalNode:
    def __init__(self, name):
        self.name = name
        self.outputs = []
        
    def connect(self, target_node):
        if target_node not in self.outputs:
            self.outputs.append(target_node)

    def send(self, color, engine, source_node=None):
        if source_node is None:
            source_node = self
        for out in self.outputs:
            engine.queue_light(out, color, source_node)

class EmitNode(OpticalNode):
    def __init__(self, name, color, is_polling=False):
        super().__init__(name)
        self.color = color
        self.is_polling = is_polling

    def fire(self, engine):
        self.send(self.color, engine)

class PulseNode(OpticalNode):
    def __init__(self, name, color_list):
        super().__init__(name)
        self.color_list = color_list
        self.current_index = 0

    def fire(self, engine):
        if self.current_index < len(self.color_list):
            self.send(self.color_list[self.current_index], engine)
            self.current_index += 1

class FilterNode(OpticalNode):
    def __init__(self, name, op, mod_color):
        super().__init__(name)
        self.op = op
        self.mod = mod_color
        self.delay_buffer = []  # FIFO buffer for delay op

    def receive(self, colors, sources, engine):
        mixed = (0,0,0)
        for c in colors: mixed = mix_lab(mixed, c)
        
        if self.op == 'delay':
            self.delay_buffer.append(mixed)
            if len(self.delay_buffer) > 1:
                res = self.delay_buffer.pop(0)
                self.send(res, engine)
            return

        L, a, b = mixed
        mL, ma, mb = self.mod
        if self.op == 'add':
            res = (L+mL, a+ma, b+mb)
        elif self.op == 'subtract':
            res = (L-mL, a-ma, b-mb)
        else:
            res = mixed
        self.send(res, engine)

class SplitterNode(OpticalNode):
    def receive(self, colors, sources, engine):
        mixed = (0,0,0)
        for c in colors: mixed = mix_lab(mixed, c)
        self.send(mixed, engine)

class LatchNode(OpticalNode):
    def __init__(self, name):
        super().__init__(name)
        self.state = (0.0, 0.0, 0.0) 
        self.poll_source = None  # Name of the polling clock emitter

    def receive(self, colors, sources, engine):
        # Only update state if the source is NOT the polling clock
        has_data = False
        mixed = (0,0,0)
        for c in colors: mixed = mix_lab(mixed, c)
        
        for src in sources:
            if src is not None and src.name != self.poll_source:
                has_data = True
                break
            
        if has_data:
            self.state = mixed
        
    def poll_state(self, engine):
        self.send(self.state, engine)

class PrismNode(OpticalNode):
    def __init__(self, name):
        super().__init__(name)
        self.routes = []

    def add_route(self, cond_func, target_name):
        self.routes.append((cond_func, target_name))

    def resolve_routes(self, nodes_dict):
        self.resolved_routes = []
        for cond, target_name in self.routes:
            if target_name in nodes_dict:
                self.resolved_routes.append((cond, nodes_dict[target_name]))

    def receive(self, colors, sources, engine):
        mixed = (0,0,0)
        for c in colors: mixed = mix_lab(mixed, c)
        
        L, a, b = mixed
        for cond_func, target in self.resolved_routes:
            if cond_func(L, a, b):
                engine.queue_light(target, mixed, self)
                return

class ObserveNode(OpticalNode):
    def __init__(self, name, console_callback, mode='default'):
        super().__init__(name)
        self.callback = console_callback
        self.mode = mode 
        self.stored_color = (0.0, 0.0, 0.0)
        self.spectrum_list = []

    def receive(self, colors, sources, engine):
        mixed = (0,0,0)
        for c in colors: mixed = mix_lab(mixed, c)
        
        if self.mode == 'accumulate':
            self.stored_color = mix_lab(self.stored_color, mixed)
            self.callback(self.stored_color, self.name, "MIX")
        elif self.mode == 'spectrum':
            self.spectrum_list.append(mixed)
            self.callback(mixed, self.name, f"SCOPE [{len(self.spectrum_list)}]")
        else:
            self.callback(mixed, self.name, "RECV")
            
        # FIX: Observe nodes now forward light so they can be used mid-chain
        self.send(mixed, engine)

# ==========================================
# 4. TIME-STEPPED GRAPH ENGINE
# ==========================================

class OpticaEngine:
    def __init__(self, output_widget, graph_scene):
        self.output = output_widget
        self.graph_scene = graph_scene
        self.nodes = {}
        self.emitters = []
        self.latches = []
        self.event_queue = {}
        self.current_frame = 0

    def build_graph(self, code):
        self.output.clear()
        self.nodes = {}
        self.emitters = []
        self.latches = []
        self.event_queue = {}
        self.current_frame = 0

        code = re.sub(r'//[^\n]*', '', code)
        
        try:
            self.parse_nodes(code)
            self.parse_wires(code)
            self.log_text("<i>Optical Graph Constructed. Ready to fire.</i>")
            self.render_graph()
        except Exception as e:
            self.log_error(f"Graph Construction Error: {str(e)}")

    def parse_nodes(self, code):
        # Emitters (Standard and Polling)
        for m in re.finditer(r'emit\s+(\w+)\s*=\s*(poll\s+)?L(-?\d+(?:\.\d+)?)\s*a(-?\d+(?:\.\d+)?)\s*b(-?\d+(?:\.\d+)?)', code):
            name, poll, l, a, b = m.groups()
            is_poll = (poll is not None)
            self.nodes[name] = EmitNode(name, (float(l), float(a), float(b)), is_polling=is_poll)
            self.emitters.append(self.nodes[name])

        # Pulse Emitters
        for m in re.finditer(r'emit\s+(\w+)\s*=\s*pulse\s*\[([^\]]+)\]', code):
            name, items_str = m.groups()
            color_list = []
            for lab_m in re.finditer(r'L(-?\d+(?:\.\d+)?)\s*a(-?\d+(?:\.\d+)?)\s*b(-?\d+(?:\.\d+)?)', items_str):
                l, a, b = lab_m.groups()
                color_list.append((float(l), float(a), float(b)))
            self.nodes[name] = PulseNode(name, color_list)
            self.emitters.append(self.nodes[name])

        # Filters
        for m in re.finditer(r'filter\s+(\w+)\s*=\s*(add|subtract|delay)\s*(?:L(-?\d+(?:\.\d+)?)\s*a(-?\d+(?:\.\d+)?)\s*b(-?\d+(?:\.\d+)?))?', code):
            name, op, l, a, b = m.groups()
            mod = (float(l or 0), float(a or 0), float(b or 0))
            self.nodes[name] = FilterNode(name, op, mod)

        # Splitters
        for m in re.finditer(r'splitter\s+(\w+)\s*=\s*split', code):
            name = m.group(1)
            self.nodes[name] = SplitterNode(name)

        # Latches (Memory)
        for m in re.finditer(r'latch\s+(\w+)\s*(?:\{([^}]*)\})?', code):
            name, body = m.groups()
            latch = LatchNode(name)
            self.nodes[name] = latch
            self.latches.append(latch)
            if body:
                poll_m = re.search(r'poll\s*:\s*(\w+)', body)
                if poll_m:
                    latch.poll_source = poll_m.group(1)

        # Observers
        for m in re.finditer(r'observe\s+(\w+)\s*=\s*(accumulate|spectrum)?', code):
            name, mode = m.groups()
            mode = mode or 'default'
            self.nodes[name] = ObserveNode(name, self.log_color, mode=mode)

        # Prisms
        for m in re.finditer(r'prism\s+(\w+)\s*\{([^}]*)\}', code):
            name, body = m.groups()
            prism = PrismNode(name)
            self.nodes[name] = prism
            for r_m in re.finditer(r'axis\s+([Lab])\s*(>=|<=|>|<|==)\s*(-?\d+(?:\.\d+)?)\s*->\s*(\w+)', body):
                axis, op, val, target = r_m.groups()
                val = float(val)
                def make_cond(ax, op, v):
                    idx = 0 if ax == 'L' else (1 if ax == 'a' else 2)
                    if op == '>': return lambda c: c[idx] > v
                    if op == '<': return lambda c: c[idx] < v
                    if op == '>=': return lambda c: c[idx] >= v
                    if op == '<=': return lambda c: c[idx] <= v
                    if op == '==': return lambda c: abs(c[idx] - v) < 0.01
                prism.add_route(make_cond(axis, op, val), target)

    def parse_wires(self, code):
        for node in self.nodes.values():
            if isinstance(node, PrismNode):
                node.resolve_routes(self.nodes)

        for m in re.finditer(r'(\w+)\s*->\s*(\w+)\s*;', code):
            src, tgt = m.groups()
            if "axis" not in m.group(0):
                if src in self.nodes and tgt in self.nodes:
                    self.nodes[src].connect(self.nodes[tgt])
                else:
                    raise Exception(f"Cannot wire {src} -> {tgt}. Node undefined.")

    def render_graph(self):
        self.graph_scene.clear()
        cx, cy = 400, 300
        radius = 200
        n = len(self.nodes)
        positions = {}
        for i, name in enumerate(self.nodes.keys()):
            angle = 2 * math.pi * i / n
            x = cx + radius * math.cos(angle)
            y = cy + radius * math.sin(angle)
            positions[name] = (x, y)
            
            # Draw Node
            rect = QGraphicsRectItem(x-60, y-25, 120, 50)
            
            # Color nodes by type
            node = self.nodes[name]
            if isinstance(node, EmitNode):
                rect.setBrush(QBrush(QColor("#E06C75"))) # Red
            elif isinstance(node, FilterNode):
                rect.setBrush(QBrush(QColor("#56B6C2"))) # Cyan
            elif isinstance(node, ObserveNode):
                rect.setBrush(QBrush(QColor("#C678DD"))) # Purple
            elif isinstance(node, LatchNode):
                rect.setBrush(QBrush(QColor("#E5C07B"))) # Yellow
            elif isinstance(node, PrismNode):
                rect.setBrush(QBrush(QColor("#98C379"))) # Green
            else:
                rect.setBrush(QBrush(QColor("#2C313C")))
                
            rect.setPen(QPen(QColor("#61AFEF"), 2))
            self.graph_scene.addItem(rect)
            
            # Draw Label
            text = QGraphicsTextItem(name)
            text.setDefaultTextColor(QColor("#FFFFFF"))
            text.setFont(QFont("Consolas", 10, QFont.Bold))
            text.setPos(x-40, y-10)
            self.graph_scene.addItem(text)
            
        # Draw Wires
        for name, node in self.nodes.items():
            src_pos = positions[name]
            for out in node.outputs:
                tgt_pos = positions[out.name]
                
                start_x = src_pos[0]
                start_y = src_pos[1]+25
                end_x = tgt_pos[0]
                end_y = tgt_pos[1]-25
                
                line = QGraphicsLineItem(start_x, start_y, end_x, end_y)
                pen = QPen(QColor("#98C379"), 2)
                line.setPen(pen)
                self.graph_scene.addItem(line)
                
                # Draw Arrowhead
                dx = end_x - start_x
                dy = end_y - start_y
                angle = math.atan2(dy, dx)
                arrow_size = 12
                p1 = QPointF(end_x, end_y)
                p2 = QPointF(end_x - arrow_size * math.cos(angle - math.pi/6), 
                             end_y - arrow_size * math.sin(angle - math.pi/6))
                p3 = QPointF(end_x - arrow_size * math.cos(angle + math.pi/6), 
                             end_y - arrow_size * math.sin(angle + math.pi/6))
                
                arrow = QPolygonF([p1, p2, p3])
                arrow_item = QGraphicsPolygonItem(arrow)
                arrow_item.setBrush(QBrush(QColor("#98C379")))
                arrow_item.setPen(QPen(QColor("#98C379")))
                self.graph_scene.addItem(arrow_item)

    def queue_light(self, target_node, color, source_node=None):
        next_frame = self.current_frame + 1
        if next_frame not in self.event_queue:
            self.event_queue[next_frame] = {}
        if target_node not in self.event_queue[next_frame]:
            self.event_queue[next_frame][target_node] = []
        self.event_queue[next_frame][target_node].append((source_node, color))

    def step(self):
        if self.current_frame == 0:
            self.log_text(f"<b>Frame 1: Firing {len(self.emitters)} Emitter(s)...</b>")
            for emitter in self.emitters:
                if not emitter.is_polling:
                    emitter.fire(self)
            self.current_frame = 1
            return True

        # Poll Latches (Memory emits its state every frame)
        for latch in self.latches:
            latch.poll_state(self)

        # Fire polling emitters every frame
        for emitter in self.emitters:
            if emitter.is_polling:
                emitter.fire(self)

        if self.current_frame not in self.event_queue or not self.event_queue[self.current_frame]:
            self.log_text("<i>Signal propagation finished. Graph is dark.</i>")
            self.current_frame += 1
            return False

        self.log_text(f"<b>Frame {self.current_frame}:</b> Processing {len(self.event_queue[self.current_frame])} node(s).")
        
        # Delete processed frame to prevent memory leak
        events = self.event_queue.pop(self.current_frame)
        for node, items in events.items():
            colors = [item[1] for item in items]
            sources = [item[0] for item in items]
            node.receive(colors, sources, self)
            
        self.current_frame += 1
        return True

    def log_color(self, lab, name, tag):
        l, a, b = lab
        r, g, bl = lab_to_rgb(l, a, b)
        html = f"""
        <div style="display:flex; align-items:center; margin-bottom:4px;">
            <div style="width:30px; height:30px; background-color:rgb({r},{g},{bl}); border:1px solid #555; margin-right:8px; border-radius:4px;"></div>
            <span style="color:#ABB2BF; font-family:monospace; font-size:12px;">
                <b style="color:#61AFEF;">{name}</b> <span style="color:#5C6370;">[{tag}]</span> &nbsp; L{l:.0f} a{a:.0f} b{b:.0f}
            </span>
        </div>
        """
        self.output.append(html)

    def log_text(self, text):
        self.output.append(f'<span style="color:#5C6370; font-family:monospace; font-size:12px;">{text}</span><br>')

    def log_error(self, msg):
        self.output.append(f'<span style="color:#ff5555; font-family:monospace; font-size:13px;">⚠ {msg}</span><br>')

# ==========================================
# 5. PROJECT CODE EXAMPLES
# ==========================================

PROJECT_SR_LATCH = """// Optica: SR Latch (Memory State Machine)
// Set pulse stores Red. Reset pulse clears to Black.
// Clock polls memory every frame and routes to output.

emit Set = pulse [L50 a80 b67];
emit Reset = pulse [L0 a0 b0];

// Polling emitter fires every frame
emit Clock = poll L50 a0 b0;

latch MemoryCell {
    poll: Clock;
}

observe Output = default;

Set -> MemoryCell;
Reset -> MemoryCell;
Clock -> MemoryCell;
MemoryCell -> Output;
"""

PROJECT_OSCILLATOR = """// Optica: Optical Oscillator
// A spark feeds an accumulator, which loops through a 1-frame delay.
// The signal grows brighter every cycle until it saturates.

emit Spark = L50 a40 b-40;

observe Plate = accumulate;
filter Fiber = delay;

Spark -> Plate;
Plate -> Fiber;
Fiber -> Plate;
"""

# ==========================================
# 6. MAIN IDE WINDOW
# ==========================================

class OpticaIDE(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Optica IDE - Memory, Logic & Graph UI")
        self.resize(1200, 800)
        self.setStyleSheet("""
            QMainWindow { background-color: #21252B; }
            QPlainTextEdit, QTextEdit { background-color: #282C34; color: #ABB2BF; border: 1px solid #3E4451; border-radius: 4px; }
            QPushButton { background-color: #61AFEF; color: black; font-weight: bold; padding: 8px; border: none; border-radius: 4px; }
            QPushButton:hover { background-color: #5290E2; }
            QPushButton:disabled { background-color: #3E4451; color: #5C6370; }
            QGraphicsView { background-color: #181A1F; border: 1px solid #3E4451; }
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)

        # Tabs
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # --- Tab 1: Editor & Console ---
        editor_tab = QWidget()
        editor_layout = QVBoxLayout(editor_tab)

        # Top Bar
        top_bar = QHBoxLayout()
        build_btn = QPushButton("🔧 Build Graph")
        build_btn.setStyleSheet("background-color: #C678DD; color: white;")
        build_btn.clicked.connect(self.build_graph)
        
        self.step_btn = QPushButton("▶ Step Frame")
        self.step_btn.setStyleSheet("background-color: #98C379; color: black;")
        self.step_btn.clicked.connect(self.step_frame)
        self.step_btn.setEnabled(False)
        
        self.run_btn = QPushButton("⏩ Run 10 Frames")
        self.run_btn.setStyleSheet("background-color: #61AFEF; color: black;")
        self.run_btn.clicked.connect(self.run_frames)
        self.run_btn.setEnabled(False)
        
        proj_btn1 = QPushButton("Proj 1: SR Latch")
        proj_btn1.clicked.connect(lambda: self.editor.setPlainText(PROJECT_SR_LATCH))
        
        proj_btn2 = QPushButton("Proj 2: Oscillator")
        proj_btn2.clicked.connect(lambda: self.editor.setPlainText(PROJECT_OSCILLATOR))

        top_bar.addWidget(build_btn)
        top_bar.addWidget(self.step_btn)
        top_bar.addWidget(self.run_btn)
        top_bar.addWidget(proj_btn1)
        top_bar.addWidget(proj_btn2)
        top_bar.addStretch()
        editor_layout.addLayout(top_bar)

        splitter = QSplitter(Qt.Vertical)
        self.editor = QPlainTextEdit()
        self.highlighter = OpticaHighlighter(self.editor.document())
        self.editor.setPlainText(PROJECT_SR_LATCH)

        self.output_console = QTextEdit()
        self.output_console.setReadOnly(True)

        splitter.addWidget(self.editor)
        splitter.addWidget(self.output_console)
        splitter.setSizes([400, 300])
        editor_layout.addWidget(splitter)
        self.tabs.addTab(editor_tab, "Editor & Console")

        # --- Tab 2: Node Graph ---
        self.graph_view = QGraphicsView()
        self.graph_scene = QGraphicsScene()
        self.graph_scene.setSceneRect(QRectF(0, 0, 800, 600))
        self.graph_view.setScene(self.graph_scene)
        self.tabs.addTab(self.graph_view, "Node Graph")

        # Engine Instance
        self.engine = OpticaEngine(self.output_console, self.graph_scene)
        self.timer = QTimer()
        self.timer.timeout.connect(self.step_frame)

    def build_graph(self):
        code = self.editor.toPlainText()
        self.engine.build_graph(code)
        self.tabs.setCurrentIndex(1) # Switch to graph view
        self.step_btn.setEnabled(True)
        self.run_btn.setEnabled(True)  # FIX: Enable run button

    def step_frame(self):
        # Handle bounded execution for the Run button
        if hasattr(self, 'run_count'):
            self.run_count += 1
            if self.run_count >= 10:
                self.timer.stop()
                self.step_btn.setText("▶ Step Frame")
                del self.run_count
                return

        if self.engine.step() is False:
            self.timer.stop()
            self.step_btn.setText("▶ Step Frame")
            if hasattr(self, 'run_count'):
                del self.run_count

    def run_frames(self):
        self.step_btn.setText("⏸ Stepping...")
        self.run_count = 0
        self.timer.start(300)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = OpticaIDE()
    window.show()
    sys.exit(app.exec())