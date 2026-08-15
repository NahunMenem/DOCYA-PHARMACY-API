# DocYa Pharmacy API

Backend independiente para el marketplace de farmacias de DocYa. Se integra con
DocYa Core mediante un puente interno de solo lectura y con la app paciente
mediante endpoints autenticados.

## Decisiones de negocio implementadas

- El pedido se ofrece primero a la sucursal habilitada, abierta y en línea más
  cercana al domicilio del paciente.
- La farmacia cobra el 100% al paciente en su propia cuenta de Mercado Pago.
- DocYa no retiene el dinero de la venta. Registra una comisión configurable,
  inicialmente del 10%, sobre el subtotal de medicamentos entregados.
- El costo de envío queda fuera de la comisión.
- La deuda se consolida mensualmente. El modelo permite bloquear una farmacia
  morosa sin cancelar pedidos ya pagados.
- El paciente solo puede seleccionar recetas reales ya emitidas a su nombre en
  DocYa (recetario o consulta). No se crean recetas de ejemplo para este flujo.
- Todos los cambios relevantes quedan preparados para auditoría en
  `order_events` y para notificaciones confiables mediante `outbox_events`.

## Aislamiento

El servicio usa su propio PostgreSQL y un pool pequeño de hasta cinco conexiones
por defecto. Nunca consulta directamente las tablas clínicas: obtiene las
recetas reales mediante un endpoint interno autenticado de DocYa Core.

## Desarrollo local

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
python scripts\migrate.py
python start.py
```

Las migraciones son explícitas: iniciar la API nunca altera la base de datos.

## Railway (cuando se autorice el despliegue)

Crear un servicio nuevo llamado `DOCYA-PHARMACY-API` desde esta carpeta y
configurar:

- `DATABASE_URL`: referencia al PostgreSQL exclusivo del servicio de farmacias.
- `CORE_API_URL`: URL interna o pública del backend DocYa Core.
- `JWT_SECRET`: exactamente el mismo secreto del backend DocYa para validar a
  los pacientes.
- `INTERNAL_API_KEY`: secreto nuevo de al menos 32 caracteres compartido solo
  con servicios internos.
- `ENVIRONMENT=production`.
- `ALLOWED_ORIGINS`: dominio del futuro panel de farmacias.
- Pre-deploy command: `python scripts/migrate.py`.

No reutilizar credenciales de Mercado Pago de DocYa. Cada farmacia deberá
autorizar su propia cuenta mediante OAuth; el webhook validado será el único
componente autorizado a llamar al endpoint interno de confirmación de pago.

## Endpoints iniciales

- `GET /health`: vida del proceso, sin consultar la base.
- `GET /ready`: comprueba conexión y migración inicial.
- `POST /v1/internal/orders`: crea el pedido y busca la sucursal más cercana.
- `POST /v1/auth/register` y `POST /v1/auth/login`: acceso del panel.
- `GET /v1/prescriptions`: recetas DocYa elegibles del paciente autenticado.
- `GET/POST /v1/orders`: listado y solicitud de cotización del paciente.
- `GET /v1/orders/{id}`: consulta del dueño del pedido.
- `POST /v1/orders/{id}/quote/accept`: aceptación por el paciente.
- `GET /v1/pharmacy/assignments`: bandeja autenticada de la farmacia.
- `GET /v1/pharmacy/me`: estado regulatorio, cuenta y sucursales.
- `PATCH /v1/pharmacy/branches/{id}/availability`: apertura y disponibilidad.
- `POST /v1/pharmacy/orders/{id}/quotes`: cotización de la farmacia asignada.
- `POST /v1/pharmacy/orders/{id}/status`: avanza preparación, despacho y entrega.
- `POST /v1/internal/orders/{id}/payment-confirmed`: solo para el adaptador de
  webhook de Mercado Pago, después de verificar firma, cuenta y monto.

## Próximos hitos

1. Alta y validación documental de farmacias y sucursales.
2. Panel web de farmacia con ofertas, cotización y preparación.
3. Worker de cascada por vencimiento y notificaciones push/WebSocket.
4. OAuth y webhooks de Mercado Pago por farmacia.
5. Seguimiento de entrega y cierre de pedido.
6. Cierre mensual, factura del 10%, gracia y bloqueo automático.
7. Integración visual en la app paciente y en el monitoreo DocYa.

## Clientes incluidos en este hito

- `../DOCYA-PHARMACY-PANEL`: panel web Next.js de registro, acceso,
  disponibilidad, pedidos y cotización.
- `../DOCYA-MAC-IOS-master/lib/screens/buy_medication_screen.dart`: sección
  Flutter del paciente para elegir una receta, solicitar y seguir cotizaciones.

## Modo de pago para pruebas

Configurar `PHARMACY_PAYMENT_MODE=simulated` permite probar el circuito completo
sin cobrar dinero. Cuando el paciente acepta una cotización, la API registra un
pago con proveedor `simulation`, marca el pedido como `paid` y devuelve
`real_charge_performed=false`. No se llama a Mercado Pago ni se usan
credenciales de cobro.

La farmacia puede avanzar luego por `preparing`, `ready_for_dispatch`,
`in_delivery` y `delivered`. Al entregar se registra la comisión de DocYa en el
ledger, pero no se factura ni se debita nada. El valor predeterminado es
`disabled`; el modo simulado debe habilitarse expresamente en el servicio de
pruebas. `mercadopago` queda reservado para la integración real futura.

Para un ambiente de prueba aislado también se puede definir
`PHARMACY_AUTO_ACTIVATE_TEST_REGISTRATIONS=true`. Así las farmacias nuevas quedan
habilitadas automáticamente y pueden abrir su sucursal desde el panel. La API
rechaza esta opción si el modo de pago no es `simulated`.
