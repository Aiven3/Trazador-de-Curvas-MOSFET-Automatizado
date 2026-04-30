import serial
import threading
import pandas as pd
import numpy as np
import os
import time
import customtkinter as ctk
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
import matplotlib.pyplot as plt
from datetime import datetime

# --- Configuración Estética ---
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")

class ArduinoTracerProV8(ctk.CTk):
    """
    Interfaz de Usuario para el Trazador de Curvas MOSFET V8.0.
    Adaptada para el modo de salida por columnas del nuevo firmware ESP32.
    """
    def __init__(self):
        super().__init__()

        self.title("MOSFET Tracer Pro V8.0 - INAOE Precision")
        self.geometry("1400x950")

        # --- Estado del Sistema ---
        self.ser = None
        self.is_connected = False
        self.is_measuring = False
        
        # Diccionario para almacenar las curvas { Vgs: DataFrame }
        self.all_curves = {} 
        
        self.setup_ui()

    def setup_ui(self):
        # Layout Principal
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        # --- Sidebar (Panel de Control) ---
        self.sidebar = ctk.CTkFrame(self, width=320, corner_radius=0)
        self.sidebar.grid(row=0, column=0, sticky="nsew")

        ctk.CTkLabel(self.sidebar, text="TRACER PRO V8.0", font=("Segoe UI", 24, "bold")).pack(pady=(30, 10))
        ctk.CTkLabel(self.sidebar, text="Modo: Columnar / Matrix", font=("Segoe UI", 12), text_color="gray").pack(pady=(0, 20))
        
        # Frame de Conexión
        self.conn_frame = ctk.CTkFrame(self.sidebar, fg_color="transparent")
        self.conn_frame.pack(pady=10, padx=20, fill="x")
        
        self.port_entry = ctk.CTkEntry(self.conn_frame, placeholder_text="Puerto COM")
        self.port_entry.insert(0, "COM7") # Ajustar según el puerto real
        self.port_entry.pack(side="left", padx=(0, 5), expand=True, fill="x")
        
        self.btn_connect = ctk.CTkButton(self.conn_frame, text="Conectar", width=90, fg_color="#27AE60", command=self.toggle_connection)
        self.btn_connect.pack(side="right")

        # Botón de Barrido Central
        self.btn_start = ctk.CTkButton(self.sidebar, text="INICIAR BARRIDO TOTAL", state="disabled", 
                                           height=50, font=("Segoe UI", 14, "bold"), fg_color="#2980B9", 
                                           command=self.request_full_sweep)
        self.btn_start.pack(pady=30, padx=20, fill="x")

        self.btn_clear = ctk.CTkButton(self.sidebar, text="Limpiar Resultados", state="disabled", command=self.clear_all)
        self.btn_clear.pack(pady=5, padx=20, fill="x")

        # Panel de Parámetros Extraídos
        self.res_frame = ctk.CTkFrame(self.sidebar, border_width=1, border_color="#333")
        self.res_frame.pack(pady=40, padx=20, fill="both")
        ctk.CTkLabel(self.res_frame, text="PARAMETRIZACIÓN SPICE", font=("Segoe UI", 13, "bold"), text_color="#E74C3C").pack(pady=10)
        
        self.lbl_vth = ctk.CTkLabel(self.res_frame, text="Vth: --- V", font=("Consolas", 16))
        self.lbl_vth.pack(pady=5)
        self.lbl_gm = ctk.CTkLabel(self.res_frame, text="gm: --- mA/V", font=("Consolas", 16))
        self.lbl_gm.pack(pady=5)

        # Botón Exportar (al final)
        self.btn_export = ctk.CTkButton(self.sidebar, text="Exportar Familia (.csv)", state="disabled", fg_color="#8E44AD", command=self.export_all)
        self.btn_export.pack(side="bottom", pady=30, padx=20, fill="x")

        # --- Panel Principal (Gráfica y Consola) ---
        self.main_panel = ctk.CTkFrame(self, fg_color="#0A0A0A")
        self.main_panel.grid(row=0, column=1, sticky="nsew", padx=15, pady=15)
        self.main_panel.grid_columnconfigure(0, weight=1)
        self.main_panel.grid_rowconfigure(0, weight=4)
        self.main_panel.grid_rowconfigure(1, weight=1)

        # Configuración Matplotlib
        plt.style.use('dark_background')
        self.fig, self.ax = plt.subplots(figsize=(8, 6), dpi=100)
        self.fig.patch.set_facecolor('#0A0A0A')
        self.ax.set_facecolor('#111111')
        self.ax.set_title("FAMILIA DE CURVAS ID vs VDS", pad=20, fontsize=16, color="#ECF0F1", weight="bold")
        self.ax.set_xlabel("Voltaje Drenador-Fuente VDS [V]", color="#BDC3C7", fontsize=12)
        self.ax.set_ylabel("Corriente de Drenador ID [mA]", color="#BDC3C7", fontsize=12)
        self.ax.grid(True, color="#2C3E50", linestyle="--", alpha=0.5)
        
        self.canvas = FigureCanvasTkAgg(self.fig, master=self.main_panel)
        self.canvas.get_tk_widget().grid(row=0, column=0, sticky="nsew", padx=20, pady=20)

        self.console = ctk.CTkTextbox(self.main_panel, font=("Consolas", 12), fg_color="#121212", text_color="#00FF41", border_width=1, border_color="#333")
        self.console.grid(row=1, column=0, sticky="nsew", padx=20, pady=(0, 20))

    def log(self, msg):
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.insert("end", f"[{ts}] {msg}\n")
        self.console.see("end")

    def toggle_connection(self):
        if not self.is_connected:
            try:
                port = self.port_entry.get()
                # Tiempo de espera generoso para evitar tramas cortadas
                self.ser = serial.Serial(port, 115200, timeout=0.1) 
                self.is_connected = True
                self.btn_connect.configure(text="Desconectar", fg_color="#C0392B")
                self.btn_start.configure(state="normal")
                self.btn_clear.configure(state="normal")
                self.log(f"Puerto {port} abierto. Tracer V8.0 detectado.")
            except Exception as e:
                self.log(f"Error de conexión: {e}")
        else:
            self.disconnect()

    def disconnect(self):
        self.is_measuring = False
        if self.ser and self.ser.is_open:
            self.ser.close()
        self.is_connected = False
        self.btn_connect.configure(text="Conectar", fg_color="#27AE60")
        self.btn_start.configure(state="disabled")
        self.log("Puerto cerrado correctamente.")

    def clear_all(self):
        self.all_curves = {}
        self.ax.clear()
        self.ax.set_title("FAMILIA DE CURVAS ID vs VDS", pad=20, fontsize=16)
        self.ax.set_xlabel("VDS [V]")
        self.ax.set_ylabel("ID [mA]")
        self.ax.grid(True, color="#2C3E50", linestyle="--")
        self.canvas.draw()
        self.lbl_vth.configure(text="Vth: --- V")
        self.lbl_gm.configure(text="gm: --- mA/V")
        self.log("Dashboard reseteado.")

    def request_full_sweep(self):
        """Envía el comando de inicio al ESP32 para que ejecute toda la secuencia."""
        if self.is_measuring: return
        self.is_measuring = True
        self.all_curves = {}
        self.btn_start.configure(state="disabled", text="BARRIDO EN PROGRESO...")
        self.log("Enviando señal de inicio... Mantenga el MOSFET refrigerado.")
        
        # Limpiar buffer de entrada y enviar un simple \n para despertar el loop
        self.ser.flushInput()
        self.ser.write(b"\n")
        
        threading.Thread(target=self.data_acquisition_task, daemon=True).start()

    def data_acquisition_task(self):
        """Hilo dedicado a escuchar la matriz de datos tabular del firmware."""
        try:
            capturing_matrix = False
            headers = []
            raw_matrix_rows = []

            while self.is_measuring:
                if self.ser.in_waiting > 0:
                    line = self.ser.readline().decode('utf-8', errors='ignore').strip()
                    
                    if not line: continue
                    
                    # Log de progreso del firmware
                    if "[Progreso]" in line:
                        self.log(line)
                    
                    # Detectar inicio de tabla
                    if ">>> DATOS PARA EXCEL" in line:
                        capturing_matrix = True
                        self.log("Recibiendo matriz de resultados...")
                        continue
                    
                    # Detectar fin de tabla
                    if ">>> FIN DE DATOS" in line:
                        self.log("Transferencia completada. Procesando...")
                        self.process_received_matrix(headers, raw_matrix_rows)
                        break

                    if capturing_matrix:
                        if not headers:
                            # La primera línea después del inicio son los encabezados
                            headers = line.split('\t')
                            # Limpiar strings vacíos al final
                            headers = [h for h in headers if h.strip()]
                        else:
                            # Líneas de datos
                            rows = line.split('\t')
                            rows = [r for r in rows if r.strip()]
                            if rows: raw_matrix_rows.append(rows)

                time.sleep(0.001)

            self.finalize_acquisition()

        except Exception as e:
            self.log(f"Error crítico en adquisición: {e}")
            self.finalize_acquisition()

    def process_received_matrix(self, headers, rows):
        """Convierte las columnas recibidas en DataFrames por cada Vgs."""
        try:
            # Determinamos cuántas curvas hay (cada curva tiene 2 columnas: VDS e ID)
            num_curves = len(headers) // 2
            
            for i in range(num_curves):
                vds_col_idx = i * 2
                id_col_idx = (i * 2) + 1
                
                header_name = headers[id_col_idx] # Ejemplo: ID_Vg2.4
                vgs_val = float(header_name.replace("ID_Vg", ""))
                
                curve_data = []
                for row in rows:
                    if len(row) > id_col_idx:
                        try:
                            vds = float(row[vds_col_idx])
                            id_m = float(row[id_col_idx])
                            # Evitar ceros basura del final de la matriz si el sweep se cortó
                            if vds == 0 and id_m == 0 and len(curve_data) > 0:
                                continue
                            curve_data.append({'VDS': vds, 'ID': id_m})
                        except: pass
                
                if curve_data:
                    self.all_curves[vgs_val] = pd.DataFrame(curve_data)

            self.log(f"Se procesaron {len(self.all_curves)} curvas exitosamente.")
            self.update_final_plot()
            self.compute_parameters()

        except Exception as e:
            self.log(f"Error procesando matriz: {e}")

    def update_final_plot(self):
        """Dibuja todas las curvas capturadas en el lienzo de Matplotlib."""
        self.ax.clear()
        self.ax.grid(True, color="#2C3E50", linestyle="--", alpha=0.5)
        self.ax.set_title("CARACTERÍSTICAS ID vs VDS - IRFZ44N", pad=20, fontsize=16)
        self.ax.set_xlabel("VDS [V]")
        self.ax.set_ylabel("ID [mA]")

        # Usar un mapa de colores profesional
        vgs_sorted = sorted(self.all_curves.keys())
        colors = plt.cm.plasma(np.linspace(0.1, 0.9, len(vgs_sorted)))

        for idx, vgs in enumerate(vgs_sorted):
            df = self.all_curves[vgs]
            self.ax.plot(df['VDS'], df['ID'], color=colors[idx], lw=2, label=f"VGS = {vgs}V")

        self.ax.legend(loc='best', fontsize='small', ncol=2)
        self.canvas.draw()

    def compute_parameters(self):
        """Extracción de Vth y gm basada en los datos reales de saturación."""
        try:
            vgs_vals = sorted(self.all_curves.keys())
            if len(vgs_vals) < 3: return
            
            # Buscamos la ID de saturación (tomando el promedio de los últimos 5 puntos para estabilidad)
            ids_sat = []
            vgs_clean = []
            
            for v in vgs_vals:
                df = self.all_curves[v]
                if not df.empty and len(df) > 5:
                    ids_sat.append(df['ID'].tail(5).mean())
                    vgs_clean.append(v)
            
            # gm (transconductancia) en el punto más alto
            gm = (ids_sat[-1] - ids_sat[-2]) / (vgs_clean[-1] - vgs_clean[-2])
            
            # Vth por extrapolación lineal de la raíz de ID
            sqrt_id = np.sqrt(np.abs(ids_sat))
            m, b = np.polyfit(vgs_clean, sqrt_id, 1)
            vth = -b/m
            
            self.lbl_vth.configure(text=f"Vth: {vth:.3f} V")
            self.lbl_gm.configure(text=f"gm: {gm:.2f} mA/V")
            self.log(f"Análisis Analítico: Vth Extraído = {vth:.2f}V")
            
        except Exception as e:
            self.log("Aviso: Datos insuficientes para cálculo de parámetros.")

    def finalize_acquisition(self):
        self.is_measuring = False
        self.btn_start.configure(state="normal", text="INICIAR BARRIDO TOTAL")
        self.btn_export.configure(state="normal")

    def export_all(self):
        if not self.all_curves:
            self.log("No hay datos para exportar.")
            return

        try:
            ts = datetime.now().strftime('%Y%m%d_%H%M%S')
            folder = f"Caracterizacion_V8_IRFZ44N_{ts}"
            os.makedirs(folder, exist_ok=True)
            
            # 1. Exportar cada curva a su propio CSV
            for vgs, df in self.all_curves.items():
                filename = os.path.join(folder, f"MOSFET_Vgs_{vgs}V.csv")
                df.to_csv(filename, index=False)
            
            # 2. Generar Reporte Técnico de Parámetros
            with open(os.path.join(folder, "Resumen_Parametros.txt"), "w") as f:
                f.write(f"INAOE - REPORTE DE CARACTERIZACION V8.0\n")
                f.write(f"Fecha: {datetime.now().strftime('%d/%m/%Y %H:%M:%S')}\n")
                f.write(f"-------------------------------------------\n")
                f.write(f"Voltaje de Umbral (Vth): {self.lbl_vth.cget('text')}\n")
                f.write(f"Transconductancia (gm): {self.lbl_gm.cget('text')}\n")
                f.write(f"-------------------------------------------\n")
                f.write(f"Resumen: Barrido exitoso de {len(self.all_curves)} niveles.\n")

            self.log(f"Exportación finalizada en: /{folder}")
            
        except Exception as e:
            self.log(f"Error al exportar: {e}")

if __name__ == "__main__":
    app = ArduinoTracerProV8()
    app.mainloop()
