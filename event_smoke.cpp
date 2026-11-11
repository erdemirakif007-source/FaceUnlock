#include <windows.h>
#include <credentialprovider.h>
#include <stdio.h>

static volatile long callbacks = 0;
class Events : public ICredentialProviderEvents {
    volatile long refs_ = 1;
public:
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid == IID_IUnknown || iid == IID_ICredentialProviderEvents) {
            *out = this; AddRef(); return S_OK;
        }
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return InterlockedIncrement(&refs_); }
    ULONG STDMETHODCALLTYPE Release() override {
        ULONG n = InterlockedDecrement(&refs_); if (!n) delete this; return n;
    }
    HRESULT STDMETHODCALLTYPE CredentialsChanged(UINT_PTR) override {
        InterlockedIncrement(&callbacks); return S_OK;
    }
};

DWORD WINAPI SendSignal(void*) {
    Sleep(500);
    HANDLE pipe = CreateFileW(L"\\\\.\\pipe\\FaceUnlock", GENERIC_WRITE, 0,
                              nullptr, OPEN_EXISTING, 0, nullptr);
    if (pipe == INVALID_HANDLE_VALUE) {
        printf("pipe open failed: %lu\n", GetLastError()); return 1;
    }
    DWORD written = 0;
    BOOL ok = WriteFile(pipe, "UNLOCK\n", 7, &written, nullptr);
    CloseHandle(pipe);
    return ok && written == 7 ? 0 : 2;
}

int wmain() {
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    const CLSID clsid = {0xfc607a37, 0xae23, 0x4b3e,
        {0x80, 0x35, 0xa1, 0x05, 0x57, 0x40, 0xb3, 0xaa}};
    ICredentialProvider* provider = nullptr;
    HRESULT hr = CoCreateInstance(clsid, nullptr, CLSCTX_INPROC_SERVER,
        IID_ICredentialProvider, reinterpret_cast<void**>(&provider));
    if (FAILED(hr)) return 1;
    if (FAILED(provider->SetUsageScenario(CPUS_LOGON, 0))) return 2;
    auto* events = new Events();
    if (FAILED(provider->Advise(events, 123))) return 3;
    DWORD hiddenCount = 99, hiddenDef = 0;
    BOOL hiddenAuto = TRUE;
    provider->GetCredentialCount(&hiddenCount, &hiddenDef, &hiddenAuto);
    if (hiddenCount != 0 || hiddenAuto) { printf("tile visible before signal
"); return 5; }
    HANDLE sender = CreateThread(nullptr, 0, SendSignal, nullptr, 0, nullptr);
    for (int i = 0; i < 100 && !callbacks; ++i) {
        MSG msg;
        while (PeekMessageW(&msg, nullptr, 0, 0, PM_REMOVE)) {
            TranslateMessage(&msg);
            DispatchMessageW(&msg);
        }
        Sleep(50);
    }
    WaitForSingleObject(sender, 5000);
    DWORD senderResult = 99;
    GetExitCodeThread(sender, &senderResult);
    CloseHandle(sender);
    DWORD count = 0, def = CREDENTIAL_PROVIDER_NO_DEFAULT;
    BOOL autoLogon = FALSE;
    provider->GetCredentialCount(&count, &def, &autoLogon);
    printf("send=%lu callbacks=%ld count=%lu default=%lu auto=%d\n",
           senderResult, callbacks, count, def, autoLogon);
    provider->UnAdvise();
    provider->Release();
    events->Release();
    CoUninitialize();
    return senderResult == 0 && callbacks == 1 && count == 1 && def == 0 && autoLogon ? 0 : 4;
}
