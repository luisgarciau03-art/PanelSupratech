# Supratech — Sistema de Operaciones para Distribuidoras Mexicanas

## ¿Qué es?

Supratech es un software SaaS de gestión operativa diseñado específicamente para **distribuidoras y mayoristas independientes en México con más de 300 SKUs**. Automatiza los procesos manuales que más tiempo y dinero cuestan: pedidos, inventario, precios, compras y finanzas.

No es un CRM. No es un ERP. Es el sistema que entiende cómo opera una distribuidora mexicana de 5 a 50 empleados.

---

## Problema que resuelve

La mayoría de distribuidoras medianas en México operan con una combinación de:
- Excel para precios y pedidos
- WhatsApp para comunicación con clientes
- Una persona que "sabe dónde está todo"

Esto genera errores de precio, pedidos mal surtidos, sobrestock, clientes perdidos por falta de seguimiento y horas de trabajo manual que se pueden automatizar.

**Costo real estimado de no tener sistema: $10,000–$25,000 MXN/mes** (tiempo de equipo + errores + clientes perdidos).

---

## Módulos del sistema

| Módulo | Función |
|--------|---------|
| **Pedidos** | Gestión de pedidos B2B con historial por cliente |
| **Inventario** | Control de stock en tiempo real, alertas de bajo inventario |
| **Compras** | Cotizaciones y pedidos a proveedores |
| **Finanzas** | Cartera vencida, líneas de crédito, balance mensual |
| **Bases de Datos** | Catálogos de productos, precios y descuentos |
| **Descuentos** | Precios diferenciados por cliente y por volumen |
| **Estado de Precios** | Validación de márgenes en tiempo real |
| **Métricas de Productos** | Rotación, desempeño por SKU |
| **Productos Olvidados** | Detección de stock sin movimiento |
| **Prospección** | Evaluación de nuevos productos a incorporar |
| **Capacidad Mensual** | Planificación de capacidad operativa |
| **Inventarios** | Dashboard de inventario con picking |
| **Analytics** | Ranking de productos, alertas de clientes inactivos |

---

## Stack técnico

- **Backend:** Python + Flask (~350K líneas) — desplegado en Railway
- **Base de datos:** Google Sheets API v4 (datos operativos por cliente) + Firestore (usuarios, health score, configuración)
- **Autenticación:** Firebase Auth (JWT)
- **Frontend:** HTML/CSS/JS vanilla (sin frameworks) + Jinja2
- **Integraciones:** Google Sheets, Google Drive, ImgBB

---

## Modelo de negocio

| Plan | Precio |
|------|--------|
| Mensual | $399 MXN/mes |
| Anual | $379 MXN/mes ($4,548 MXN/año) |

**Onboarding e integración: gratis** en ambos planes.

---

## Sistema de prospección (PanelSupratech)

App independiente para el equipo de ventas de Supratech. Gestiona el pipeline de clientes potenciales en dos etapas:

1. **Etapa Filtro** — Llamada inicial al negocio para obtener el contacto del dueño/gerente (quien toma decisiones)
2. **Etapa Pitch** — Llamada directa al decisor con script de venta y calificación

Prospectos se importan automáticamente desde Google Maps (ferreterías, tornillerías, distribuidoras de materiales, abarrotes, consumibles, refacciones, plásticos, etc.) filtrando por +100 reseñas y ordenados de mayor a menor opiniones.

---

## Perfil de cliente ideal (ICP)

- **Tipo:** Negocio independiente (no franquicia ni cadena)
- **Tamaño:** 5 a 50 empleados
- **Catálogo:** +300 SKUs
- **Ubicación:** México (inicio en Guadalajara, Jalisco)
- **Giros:** Ferretería, tornillería, materiales de construcción, abarrotes, consumibles industriales, refacciones, plásticos, farmacéutica, papelería y cualquier distribuidora con catálogo amplio

---

## URLs en producción

| Sistema | URL |
|---------|-----|
| Landing | https://web-production-a9447.up.railway.app/ |
| App (clientes) | https://web-production-a9447.up.railway.app/panel |
| Admin Health Score | https://web-production-a9447.up.railway.app/admin/health |
| ROI Calculator | https://web-production-a9447.up.railway.app/roi |
| Blog SEO | https://web-production-a9447.up.railway.app/blog |
| Panel Prospectos | https://panelsupratech-production.up.railway.app/ |

---

## Repositorios

| Repo | Contenido |
|------|-----------|
| [Supratech](https://github.com/luisgarciau03-art/Supratech) | App principal (clientes, landing, admin) |
| [PanelSupratech](https://github.com/luisgarciau03-art/PanelSupratech) | Sistema de prospección y llamadas |
