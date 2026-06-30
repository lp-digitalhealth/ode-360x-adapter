# tools/

Test/reference utilities that are not part of the adapter package.

## stub_360x_server.py
Stands in for the medical-side Referral Initiator's 360X endpoint, over plain HTTP,
so the adapter's **outbound** path can be tested without Direct/HISP/XDM.

```
adapter  --(http transport)-->  POST /360x/receive  -->  stub_360x_server
```

Run it (from this `python/` directory):
```bash
pip install -e ".[server,fhir]"
uvicorn tools.stub_360x_server:app --port 9000
```
Then run the adapter with the `http` transport pointed at it:
```bash
export ODE_ADAPTER_IHE_TRANSPORT=http
export ODE_ADAPTER_IHE_OUTBOUND_URL=http://localhost:9000/360x/receive
```
Inspect what it received: `GET http://localhost:9000/360x/received`
