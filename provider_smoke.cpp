#include <windows.h>
#include <credentialprovider.h>
#include <shlguid.h>
#include <stdio.h>
#pragma comment(lib, "uuid.lib")

int wmain() {
    HMODULE dll = LoadLibraryW(L"FaceUnlockCP.dll");
    if (!dll) return 1;
    using GetClass = HRESULT (STDAPICALLTYPE*)(REFCLSID, REFIID, void**);
    using CanUnload = HRESULT (STDAPICALLTYPE*)();
    auto getClass = reinterpret_cast<GetClass>(GetProcAddress(dll, "DllGetClassObject"));
    auto canUnload = reinterpret_cast<CanUnload>(GetProcAddress(dll, "DllCanUnloadNow"));
    if (!getClass || !canUnload) return 2;
    const CLSID clsid = {0xfc607a37, 0xae23, 0x4b3e,
        {0x80, 0x35, 0xa1, 0x05, 0x57, 0x40, 0xb3, 0xaa}};
    IClassFactory* factory = nullptr;
    HRESULT hr = getClass(clsid, IID_IClassFactory, reinterpret_cast<void**>(&factory));
    if (FAILED(hr) || !factory) return 3;
    ICredentialProvider* provider = nullptr;
    hr = factory->CreateInstance(nullptr, IID_ICredentialProvider,
                                 reinterpret_cast<void**>(&provider));
    if (FAILED(hr) || !provider) return 4;
    hr = provider->SetUsageScenario(CPUS_UNLOCK_WORKSTATION, 0);
    if (FAILED(hr)) return 5;
    DWORD fields = 0, count = 99, def = 99;
    BOOL autoLogon = TRUE;
    provider->GetFieldDescriptorCount(&fields);
    provider->GetCredentialCount(&count, &def, &autoLogon);
    printf("fields=%lu credentials=%lu default=%lu auto=%d\n", fields, count, def, autoLogon);
    if (fields != 3 || count > 1 || autoLogon) return 6;
    for (DWORD id = 0; id < fields; ++id) {
        CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR* field = nullptr;
        if (FAILED(provider->GetFieldDescriptorAt(id, &field)) || !field) return 12;
        bool valid = field->dwFieldID == id &&
            (id != 0 || (field->cpft == CPFT_TILE_IMAGE &&
                         field->guidFieldType == CPFG_CREDENTIAL_PROVIDER_LOGO)) &&
            (id != 1 || (field->cpft == CPFT_SMALL_TEXT &&
                         field->guidFieldType == CPFG_CREDENTIAL_PROVIDER_LABEL));
        CoTaskMemFree(field->pszLabel);
        CoTaskMemFree(field);
        if (!valid) return 13;
    }
    if (count == 1) {
        ICredentialProviderCredential* credential = nullptr;
        if (FAILED(provider->GetCredentialAt(0, &credential)) || !credential) return 10;
        HBITMAP image = nullptr;
        if (FAILED(credential->GetBitmapValue(0, &image)) || !image) return 14;
        DeleteObject(image);
        PWSTR label = nullptr;
        if (FAILED(credential->GetStringValue(1, &label)) || !label) return 15;
        CoTaskMemFree(label);
        credential->Release();
    }
    hr = provider->SetUsageScenario(CPUS_LOGON, 0);
    if (FAILED(hr)) return 8;
    provider->GetCredentialCount(&count, &def, &autoLogon);
    if (count > 1 || autoLogon) return 9;
    provider->Release();
    factory->Release();
    if (canUnload() != S_OK) return 7;
    FreeLibrary(dll);
    CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    ICredentialProvider* registered = nullptr;
    hr = CoCreateInstance(clsid, nullptr, CLSCTX_INPROC_SERVER,
                          IID_ICredentialProvider, reinterpret_cast<void**>(&registered));
    printf("registered COM hr=0x%08lX\n", static_cast<unsigned long>(hr));
    if (FAILED(hr) || !registered) return 11;
    registered->Release();
    CoUninitialize();
    return 0;
}
