import sys
import re
import math
import numpy as np
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QPlainTextEdit, QPushButton, QTextEdit, QTabWidget, QFrame, QLabel, QSplitter
)
from PySide6.QtGui import QColor, QTextCharFormat, QFont, QSyntaxHighlighter
from PySide6.QtCore import Qt, QRegularExpression

# 3D Graphing imports
import pyqtgraph as pg
import pyqtgraph.opengl as gl

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
    return ((lab1[0] + lab2[0]) / 2.0, (lab1[1] + lab2[1]) / 2.0, (lab1[2] + lab2[2]) / 2.0)

def complement_lab(lab):
    return (100 - lab[0], -lab[1], -lab[2])

# ==========================================
# 2. CHROMA SYNTAX HIGHLIGHTER
# ==========================================

class ChromaHighlighter(QSyntaxHighlighter):
    def __init__(self, document):
        super().__init__(document)
        self.rules = []

        keyword_format = QTextCharFormat()
        keyword_format.setForeground(QColor("#C678DD"))
        keyword_format.setFontWeight(QFont.Bold)
        keywords = r'\b(pigment|cast|illuminate|shadow|gradient|prism|diffuse|palette|textile|mosaic|compose|tint|observe|plot|spectrum|from|to|step|swatch|window|render|add|length|halt)\b'
        self.rules.append((QRegularExpression(keywords), keyword_format))

        lab_format = QTextCharFormat()
        lab_format.setForeground(QColor("#D19A66"))
        self.rules.append((QRegularExpression(r'\b[Lab]-?\d+(?:\.\d+)?\b'), lab_format))

        comment_format = QTextCharFormat()
        comment_format.setForeground(QColor("#5C6370"))
        comment_format.setFontItalic(True)
        self.rules.append((QRegularExpression(r'//[^\n]*'), comment_format))

        string_format = QTextCharFormat()
        string_format.setForeground(QColor("#98C379"))
        self.rules.append((QRegularExpression(r'"[^"]*"'), string_format))

    def highlightBlock(self, text):
        for pattern, fmt in self.rules:
            matchIterator = pattern.globalMatch(text)
            while matchIterator.hasNext():
                match = matchIterator.next()
                self.setFormat(match.capturedStart(), match.capturedLength(), fmt)

# ==========================================
# 3. CHROMA BLOCK-BASED INTERPRETER
# ==========================================

class ChromaInterpreter:
    def __init__(self, output_widget, plot_widget=None, gui_widget=None):
        self.output = output_widget
        self.plot_widget = plot_widget
        self.gui_widget = gui_widget
        self.global_vars = {'RED': (53.0, 80.0, 67.0), 'GREEN': (50.0, -128.0, 0.0), 'BLUE': (50.0, 0.0, -128.0), 'NULL': (0.0,0.0,0.0)}
        self.functions = {}
        
    def execute(self, code):
        self.output.clear()
        self.global_vars = {'RED': (53.0, 80.0, 67.0), 'GREEN': (50.0, -128.0, 0.0), 'BLUE': (50.0, 0.0, -128.0), 'NULL': (0.0,0.0,0.0)}
        self.functions = {}

        if self.plot_widget: self.plot_widget.clear_points()
        if self.gui_widget:
            layout = self.gui_widget.layout()
            if layout:
                while layout.count():
                    child = layout.takeAt(0)
                    if child.widget(): child.widget().deleteLater()

        code = re.sub(r'//[^\n]*', '', code)
        tokens = self.tokenize(code)
        
        try:
            self.eval_block(tokens, self.global_vars)
        except Exception as e:
            self.log_error(f"Runtime Error: {str(e)}")

    def tokenize(self, code):
        tokens = re.split(r'(\s+|[\{\};])', code)
        return [t.strip() for t in tokens if t.strip()]

    def eval_block(self, tokens, vars_dict):
        i = 0
        while i < len(tokens):
            token = tokens[i]
            
            if token == 'pigment':
                var_name = tokens[i+1]
                if tokens[i+2] != '=': raise Exception("Expected '=' after pigment")
                expr_tokens = []
                j = i + 3
                while tokens[j] != ';':
                    expr_tokens.append(tokens[j])
                    j += 1
                vars_dict[var_name] = self.eval_expr(' '.join(expr_tokens), vars_dict)
                i = j + 1
                
            elif token == 'palette':
                var_name = tokens[i+1]
                if tokens[i+2] != '=': raise Exception("Expected '=' after palette")
                vars_dict[var_name] = []
                i = i + 3
                if tokens[i] == '[' and tokens[i+1] == ']': i += 2
                if tokens[i] == ';': i += 1

            elif token == 'tint':
                # Mutation: tint var = expr;
                m = re.match(r'^(\w+)\s*=\s*(.+)', ' '.join(tokens[i+1:i+10]))
                if m and '=' in tokens[i+2:i+5]:
                    var_name = m.group(1)
                    expr_str = m.group(2)
                    val = self.eval_expr(expr_str, vars_dict)
                    
                    # Array element assignment: tint arr[0] = expr;
                    arr_m = re.match(r'(\w+)\[(\d+)\]', var_name)
                    if arr_m:
                        arr_name, idx = arr_m.groups()
                        if arr_name in vars_dict and isinstance(vars_dict[arr_name], list):
                            vars_dict[arr_name][int(idx)] = val
                    else:
                        vars_dict[var_name] = val
                    
                    # Fast forward to ';'
                    while tokens[i] != ';': i += 1
                    i += 1
                # Append: tint arr + expr;
                elif tokens[i+2] == '+':
                    arr_name = tokens[i+1]
                    expr_str = ' '.join(tokens[i+3:tokens.index(';', i)])
                    val = self.eval_expr(expr_str, vars_dict)
                    if arr_name in vars_dict and isinstance(vars_dict[arr_name], list):
                        vars_dict[arr_name].append(val)
                    while tokens[i] != ';': i += 1
                    i += 1
                else:
                    i += 1
             
            elif token == 'cast':
                expr_tokens = []
                j = i + 1
                while tokens[j] != ';':
                    expr_tokens.append(tokens[j])
                    j += 1
                arg = ' '.join(expr_tokens)
                self.handle_cast(arg, vars_dict)
                i = j + 1

            elif token == 'plot':
                var_name = tokens[i+1]
                if var_name in vars_dict and isinstance(vars_dict[var_name], tuple):
                    self.plot_color(vars_dict[var_name], var_name)
                i += 3

            elif token == 'halt':
                return # Exits current block execution

            elif token == 'gradient':
                if tokens[i+1] != '(': raise Exception("Expected '(' after gradient")
                var_name = tokens[i+2]
                if tokens[i+3] != 'from': raise Exception("Expected 'from'")
                start_val = self.eval_expr(tokens[i+4], vars_dict)[0]
                if tokens[i+5] != 'to': raise Exception("Expected 'to'")
                # Find end of 'to' expression (might be a variable or length())
                expr_end_idx = tokens.index(')', i+6)
                end_val = self.eval_expr(' '.join(tokens[i+6:expr_end_idx]), vars_dict)[0]
                
                if tokens[expr_end_idx+1] != '{': raise Exception("Expected '{' after gradient condition")
                block_start = expr_end_idx + 2
                block_end = self.find_matching_brace(tokens, block_start - 1)
                block_tokens = tokens[block_start:block_end]
                
                step = 1 if end_val > start_val else -1
                for l_val in range(int(start_val), int(end_val) + step, step):
                    loop_vars = vars_dict.copy()
                    loop_vars[var_name] = (float(l_val), 0.0, 0.0)
                    self.eval_block(block_tokens, loop_vars)
                    if not loop_vars.get('__halt__', False):
                        vars_dict[var_name] = loop_vars.get(var_name) # Persist loop var changes if mutated
                
                i = block_end + 2

            elif token == 'illuminate':
                if tokens[i+1] != '(': raise Exception("Expected '(' after illuminate")
                expr_end = tokens.index(')', i+2)
                expr_str = ' '.join(tokens[i+2:expr_end])
                condition_val = self.eval_expr(expr_str, vars_dict)
                
                if tokens[expr_end+1] != '{': raise Exception("Expected '{' after illuminate condition")
                block_start = expr_end + 2
                block_end = self.find_matching_brace(tokens, block_start - 1)
                if_block = tokens[block_start:block_end]
                
                shadow_block = []
                if block_end + 1 < len(tokens) and tokens[block_end+1] == 'shadow':
                    if tokens[block_end+2] != '{': raise Exception("Expected '{' after shadow")
                    s_block_start = block_end + 3
                    s_block_end = self.find_matching_brace(tokens, s_block_start - 1)
                    shadow_block = tokens[s_block_start:s_block_end]
                    block_end = s_block_end

                # Lightness >= 50 is True
                if condition_val[0] >= 50.0:
                    self.eval_block(if_block, vars_dict)
                else:
                    self.eval_block(shadow_block, vars_dict)
                
                i = block_end + 2

            elif token == 'swatch':
                name = tokens[i+1]
                if tokens[i+2] == '=' and tokens[i+3] == 'compose':
                    block_start = tokens.index('{', i+4) + 1
                    block_end = self.find_matching_brace(tokens, block_start - 1)
                    props_str = ' '.join(tokens[block_start:block_end])
                    self.parse_and_render_swatch(props_str, vars_dict)
                    i = block_end + 2
                else:
                    i += 1
            
            else:
                i += 1

    def find_matching_brace(self, tokens, start_index):
        count = 1
        i = start_index + 1
        while i < len(tokens):
            if tokens[i] == '{': count += 1
            elif tokens[i] == '}':
                count -= 1
                if count == 0: return i
            i += 1
        raise Exception("Missing closing brace '}'")

    def eval_expr(self, expr, vars_dict):
        expr = expr.strip()
        
        # Lab Value
        m = re.match(r'L(-?\d+(?:\.\d+)?)\s*a(-?\d+(?:\.\d+)?)\s*b(-?\d+(?:\.\d+)?)', expr)
        if m: return tuple(float(x) for x in m.groups())

        # Length Function: length(arr)
        m = re.match(r'length\((\w+)\)', expr)
        if m:
            arr = vars_dict.get(m.group(1), [])
            return (float(len(arr)), 0.0, 0.0)

        # Array Access: arr[0]
        m = re.match(r'(\w+)\[(\d+)\]', expr)
        if m:
            arr_name, idx = m.groups()
            if arr_name in vars_dict and isinstance(vars_dict[arr_name], list):
                if int(idx) < len(vars_dict[arr_name]):
                    return vars_dict[arr_name][int(idx)]
            return (0.0, 0.0, 0.0) # NULL

        # Lightness Comparison: >>
        m = re.match(r'(\w+|\S+)\s*>>\s*(\w+|\S+)', expr)
        if m:
            v1 = self.eval_expr(m.group(1), vars_dict)
            v2 = self.eval_expr(m.group(2), vars_dict)
            return (100.0, 0.0, 0.0) if v1[0] > v2[0] else (0.0, 0.0, 0.0)

        # Lightness Comparison: <<
        m = re.match(r'(\w+|\S+)\s*<<\s*(\w+|\S+)', expr)
        if m:
            v1 = self.eval_expr(m.group(1), vars_dict)
            v2 = self.eval_expr(m.group(2), vars_dict)
            return (100.0, 0.0, 0.0) if v1[0] < v2[0] else (0.0, 0.0, 0.0)

        # Lightness Math: <+>
        m = re.match(r'(.+)\s*<\+>\s*(.+)', expr)
        if m:
            v1 = self.eval_expr(m.group(1).strip(), vars_dict)
            v2 = self.eval_expr(m.group(2).strip(), vars_dict)
            return (v1[0] + v2[0], 0.0, 0.0)

        # Lightness Math: <->
        m = re.match(r'(.+)\s*<->\s*(.+)', expr)
        if m:
            v1 = self.eval_expr(m.group(1).strip(), vars_dict)
            v2 = self.eval_expr(m.group(2).strip(), vars_dict)
            return (v1[0] - v2[0], 0.0, 0.0)

        # Additive Mix: var1 + var2
        m = re.match(r'(.+)\s*\+\s*(.+)', expr)
        if m:
            v1 = self.eval_expr(m.group(1).strip(), vars_dict)
            v2 = self.eval_expr(m.group(2).strip(), vars_dict)
            return mix_lab(v1, v2)

        # Complement: ~var
        m = re.match(r'~(\w+)', expr)
        if m: return complement_lab(self.eval_expr(m.group(1), vars_dict))

        # Variable lookup
        if expr in vars_dict: return vars_dict[expr]

        raise ValueError(f"Cannot evaluate: {expr}")

    def handle_cast(self, arg, vars_dict):
        arg = arg.strip()
        if arg.startswith('spectrum("') and arg.endswith('")'):
            self.log_text(arg[10:-2])
        elif arg in vars_dict and isinstance(vars_dict[arg], tuple):
            self.log_color(vars_dict[arg], arg)
        else:
            try:
                val = self.eval_expr(arg, vars_dict)
                if isinstance(val, tuple):
                    self.log_color(val, "expr")
            except:
                self.log_error(f"Cannot cast unknown value: {arg}")

    def parse_and_render_swatch(self, props_str, vars_dict):
        if not self.gui_widget: return
        data = {}
        m = re.search(r'fill:\s*([^,\s]+)', props_str)
        if m: 
            try: data['fill'] = self.eval_expr(m.group(1), vars_dict)
            except: data['fill'] = (50,0,0)
        m = re.search(r'size:\s*\[L(-?\d+),\s*L(-?\d+)\]', props_str)
        if m: data['size'] = (int(m.group(1)) * 3, int(m.group(2)) * 3)
        
        frame = QFrame()
        l, a, b = data.get('fill', (50,0,0))
        r, g, bl = lab_to_rgb(l, a, b)
        frame.setStyleSheet(f"background-color: rgb({r},{g},{bl}); border: 1px solid #333; border-radius: 4px;")
        if 'size' in data: frame.setFixedSize(data['size'][0], data['size'][1])
        self.gui_widget.layout().addWidget(frame)

    def plot_color(self, lab, name):
        if not self.plot_widget: return
        self.plot_widget.add_point(lab[1], lab[2], lab[0], name)

    def log_color(self, lab, name):
        l, a, b = lab
        r, g, bl = lab_to_rgb(l, a, b)
        html = f"""
        <div style="display:flex; align-items:center; margin-bottom:8px;">
            <div style="width:40px; height:40px; background-color:rgb({r},{g},{bl}); border:1px solid #555; margin-right:10px; border-radius:4px;"></div>
            <span style="color:#ABB2BF; font-family:monospace; font-size:13px;">
                <b style="color:#61AFEF;">{name}</b> &nbsp; L{l:.0f} a{a:.0f} b{b:.0f}
            </span>
        </div>
        """
        self.output.append(html)

    def log_text(self, text):
        self.output.append(f'<span style="color:#ABB2BF; font-family:monospace; font-size:13px;">{text}</span><br>')

    def log_error(self, msg):
        self.output.append(f'<span style="color:#ff5555; font-family:monospace; font-size:13px;">⚠ {msg}</span><br>')


# ==========================================
# 4. 3D CIELAB VISUALIZER WIDGET
# ==========================================

class CIELAB3DWidget(gl.GLViewWidget):
    def __init__(self):
        super().__init__()
        self.setCameraPosition(distance=300, elevation=20, azimuth=45)
        self.opts['bgcolor'] = (0.13, 0.14, 0.17, 1)
        self.draw_axes()

    def draw_axes(self):
        self.addItem(gl.GLLinePlotItem(pos=np.array([[-128, 0, 0], [127, 0, 0]]), color=(1, 0, 0, 0.5), width=1, mode='lines'))
        self.addItem(gl.GLLinePlotItem(pos=np.array([[0, -128, 0], [0, 127, 0]]), color=(0, 1, 0, 0.5), width=1, mode='lines'))
        self.addItem(gl.GLLinePlotItem(pos=np.array([[0, 0, 0], [0, 0, 100]]), color=(1, 1, 1, 0.5), width=1, mode='lines'))

    def clear_points(self):
        for item in self.items[:]:
            if isinstance(item, gl.GLScatterPlotItem): self.removeItem(item)

    def add_point(self, a, b, l, name):
        r, g, bl = lab_to_rgb(l, a, b)
        pos = np.array([[a, b, l]])
        color = np.array([[r/255, g/255, bl/255, 1.0]])
        self.addItem(gl.GLScatterPlotItem(pos=pos, color=color, size=15))


# ==========================================
# 5. PROJECT CODE EXAMPLES
# ==========================================

PROJECT_FIBONACCI = """// Chroma Math: Fibonacci Sequence
// Calculates Fibonacci using Lightness (L) values.
// Stops when Lightness exceeds 100 (Out of Gamut).

pigment a = L0 a0 b0; // 0
pigment b = L1 a0 b0; // 1

cast spectrum("Fibonacci Sequence in CIELAB:");
cast a;
cast b;

// Loop 10 times
gradient (i from L0 to L10) {
    // Next number is a + b (Lightness Math)
    pigment next = a <+> b;
    
    // If Lightness > 100, we hit gamut limit
    illuminate (next >> L100) {
        cast spectrum("Gamut limit reached!");
        halt;
    } shadow {
        cast next;
        // Shift variables (Mutation)
        tint a = b;
        tint b = next;
    }
}
"""

PROJECT_BUBBLE_SORT = """// Chroma Algorithm: Bubble Sort by Lightness
// Sorts an array of colors from darkest to lightest

palette data = [];
tint data + L80 a40 b20; // Light
tint data + L20 a-50 b60; // Dark
tint data + L50 a60 b-60; // Medium
tint data + L10 a0 b0;    // Very Dark

pigment n = length(data);

// Plot unsorted data
gradient (i from L0 to n) {
    plot data[i];
}

// Bubble Sort Logic
gradient (i from L0 to n) {
    gradient (j from L0 to n <-> i) {
        pigment val1 = data[j];
        pigment val2 = data[j <+> L1];
        
        // If val1 is lighter than val2, swap them (descending order)
        illuminate (val1 >> val2) {
            tint data[j] = val2;
            tint data[j <+> L1] = val1;
        }
    }
}

// Output sorted data
cast spectrum("Sorted Colors (Darkest to Lightest):");
gradient (k from L0 to n) {
    cast data[k];
}
"""

PROJECT_UI_GENERATOR = """// Accessible UI Generator
// Generates a UI swatch based on a background color

pigment bg = L20 a20 b-20; // Dark blue background
swatch bgPanel = compose Swatch {
    fill: bg,
    size: [L100, L60]
};
tint window.add(bgPanel);

illuminate (bg << L50) {
    swatch txtPanel = compose Swatch {
        fill: L100 a0 b0, // White text
        size: [L80, L20]
    };
    tint window.add(txtPanel);
} shadow {
    swatch txtPanel = compose Swatch {
        fill: L0 a0 b0, // Black text
        size: [L80, L20]
    };
    tint window.add(txtPanel);
}

render window;
"""

# ==========================================
# 6. MAIN IDE WINDOW
# ==========================================

class ChromaIDE(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Chroma IDE - Advanced Color Computation")
        self.resize(1200, 800)
        self.setStyleSheet("""
            QMainWindow { background-color: #21252B; }
            QPlainTextEdit, QTextEdit { background-color: #282C34; color: #ABB2BF; border: 1px solid #3E4451; border-radius: 4px; }
            QPushButton { background-color: #61AFEF; color: black; font-weight: bold; padding: 10px; border: none; border-radius: 4px; }
            QPushButton:hover { background-color: #5290E2; }
            QTabWidget::pane { border: 1px solid #3E4451; }
            QTabBar::tab { background: #2C313C; color: #ABB2BF; padding: 8px 16px; border: 1px solid #3E4451; }
            QTabBar::tab:selected { background: #282C34; color: #61AFEF; }
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        
        self.tabs = QTabWidget()
        main_layout.addWidget(self.tabs)

        # --- Tab 1: Editor & Console ---
        editor_tab = QWidget()
        editor_layout = QHBoxLayout(editor_tab)
        
        left_split = QSplitter(Qt.Vertical)
        
        self.editor = QPlainTextEdit()
        self.highlighter = ChromaHighlighter(self.editor.document())
        self.editor.setPlainText(PROJECT_FIBONACCI)
        
        run_btn = QPushButton("▶ Run Chroma Code")
        run_btn.clicked.connect(self.run_code)
        
        btn_container = QWidget()
        btn_layout = QHBoxLayout(btn_container)
        btn_layout.setContentsMargins(0,0,0,0)
        btn_layout.addWidget(run_btn)
        
        proj_btn1 = QPushButton("Proj 1: Fibonacci Math")
        proj_btn1.setStyleSheet("background-color: #98C379; color: black;")
        proj_btn1.clicked.connect(lambda: self.editor.setPlainText(PROJECT_FIBONACCI))
        proj_btn2 = QPushButton("Proj 2: Bubble Sort Array")
        proj_btn2.setStyleSheet("background-color: #98C379; color: black;")
        proj_btn2.clicked.connect(lambda: self.editor.setPlainText(PROJECT_BUBBLE_SORT))
        proj_btn3 = QPushButton("Proj 3: UI Generator")
        proj_btn3.setStyleSheet("background-color: #98C379; color: black;")
        proj_btn3.clicked.connect(lambda: self.editor.setPlainText(PROJECT_UI_GENERATOR))
        
        btn_layout.addWidget(proj_btn1)
        btn_layout.addWidget(proj_btn2)
        btn_layout.addWidget(proj_btn3)
        
        left_widget = QWidget()
        l_layout = QVBoxLayout(left_widget)
        l_layout.setContentsMargins(0,0,0,0)
        l_layout.addWidget(self.editor)
        l_layout.addWidget(btn_container)
        
        self.output_console = QTextEdit()
        self.output_console.setReadOnly(True)
        
        left_split.addWidget(left_widget)
        left_split.addWidget(self.output_console)
        left_split.setSizes([500, 200])
        
        editor_layout.addWidget(left_split)
        self.tabs.addTab(editor_tab, "Editor & Console")

        # --- Tab 2: 3D Color Space ---
        self.plot_widget = CIELAB3DWidget()
        self.tabs.addTab(self.plot_widget, "3D Color Space (Memory)")

        # --- Tab 3: Live App Runner ---
        self.gui_widget = QWidget()
        self.gui_layout = QHBoxLayout(self.gui_widget)
        self.gui_layout.setAlignment(Qt.AlignLeft | Qt.AlignTop)
        self.gui_layout.setSpacing(10)
        self.gui_widget.setStyleSheet("background-color: #181A1F;")
        self.tabs.addTab(self.gui_widget, "Live App Runner (GUI)")

    def run_code(self):
        code = self.editor.toPlainText()
        self.output_console.clear()
        
        interpreter = ChromaInterpreter(
            self.output_console, 
            self.plot_widget, 
            self.gui_widget
        )
        interpreter.execute(code)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ChromaIDE()
    window.show()
    sys.exit(app.exec())