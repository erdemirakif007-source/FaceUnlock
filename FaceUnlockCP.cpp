// Event-driven Windows credential provider for the current workstation unlock.
// The provider never replaces or filters Microsoft's PIN/password providers.
#define WIN32_NO_STATUS
#include <windows.h>
#undef WIN32_NO_STATUS
#include <ntstatus.h>
#include <credentialprovider.h>
#include <propkey.h>
#include <shlguid.h>
#include <ntsecapi.h>
#include <sddl.h>
#include <shlwapi.h>
#include <strsafe.h>
#include <string>
#include <vector>
#include <new>
#include "helpers.h"

#pragma comment(lib, "advapi32.lib")
#pragma comment(lib, "secur32.lib")
#pragma comment(lib, "shlwapi.lib")
#pragma comment(lib, "credui.lib")
#pragma comment(lib, "ole32.lib")
#pragma comment(lib, "user32.lib")
#pragma comment(lib, "gdi32.lib")
#pragma comment(lib, "uuid.lib")

// {FC607A37-AE23-4B3E-8035-A1055740B3AA}
static const CLSID CLSID_FaceUnlock =
    {0xfc607a37, 0xae23, 0x4b3e, {0x80, 0x35, 0xa1, 0x05, 0x57, 0x40, 0xb3, 0xaa}};
static constexpr wchar_t REG_KEY[] = L"SOFTWARE\\FaceUnlock";
static constexpr wchar_t SECRET_NAME[] = L"L$FaceUnlockPassword";
static constexpr wchar_t PIPE_NAME[] = L"\\\\.\\pipe\\FaceUnlock";
static volatile long g_objects = 0;
static volatile long g_auth_failed = 0;
static HINSTANCE g_instance = nullptr;
static constexpr UINT WM_FACE_UNLOCK = WM_APP + 0x47;
static constexpr UINT_PTR READY_EXPIRED_TIMER = 1;
static constexpr DWORD READY_MS = 5000;

static void Trace(const wchar_t* message) {
    CreateDirectoryW(L"C:\\ProgramData\\FaceUnlock", nullptr);
    HANDLE file = CreateFileW(L"C:\\ProgramData\\FaceUnlock\\cp.log", FILE_APPEND_DATA,
                              FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_ALWAYS,
                              FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) return;
    SYSTEMTIME now{}; GetLocalTime(&now);
    wchar_t line[256]{};
    if (SUCCEEDED(StringCchPrintfW(line, ARRAYSIZE(line),
        L"%04u-%02u-%02u %02u:%02u:%02u %ls\r\n",
        now.wYear, now.wMonth, now.wDay, now.wHour, now.wMinute, now.wSecond, message))) {
        DWORD written = 0;
        WriteFile(file, line, static_cast<DWORD>(wcslen(line) * sizeof(wchar_t)), &written, nullptr);
    }
    CloseHandle(file);
}

static bool ReadConfig(const wchar_t* name, wchar_t* out, DWORD chars) {
    DWORD bytes = chars * sizeof(wchar_t);
    return RegGetValueW(HKEY_LOCAL_MACHINE, REG_KEY, name, RRF_RT_REG_SZ,
                        nullptr, out, &bytes) == ERROR_SUCCESS && out[0];
}

// Accept signals only from the installed agent executable. Its folder and the
// AgentPath value are admin-writable only, so a user-level program cannot pose
// as the agent by running a script under the user's own Python.
static bool IsTrustedClient(HANDLE pipe) {
    wchar_t expected[MAX_PATH]{};
    if (!ReadConfig(L"AgentPath", expected, ARRAYSIZE(expected))) return false;
    ULONG pid = 0;
    if (!GetNamedPipeClientProcessId(pipe, &pid)) return false;
    HANDLE process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, FALSE, pid);
    if (!process) return false;
    wchar_t image[MAX_PATH]{};
    DWORD chars = ARRAYSIZE(image);
    bool ok = QueryFullProcessImageNameW(process, 0, image, &chars) &&
              CompareStringOrdinal(image, -1, expected, -1, TRUE) == CSTR_EQUAL;
    CloseHandle(process);
    return ok;
}

static bool ReadPassword(std::vector<wchar_t>& out) {
    LSA_OBJECT_ATTRIBUTES attributes{};
    LSA_HANDLE policy = nullptr;
    if (LsaOpenPolicy(nullptr, &attributes, POLICY_GET_PRIVATE_INFORMATION, &policy) != 0)
        return false;
    LSA_UNICODE_STRING key{};
    key.Buffer = const_cast<PWSTR>(SECRET_NAME);
    key.Length = static_cast<USHORT>(wcslen(SECRET_NAME) * sizeof(wchar_t));
    key.MaximumLength = key.Length;
    PLSA_UNICODE_STRING data = nullptr;
    NTSTATUS status = LsaRetrievePrivateData(policy, &key, &data);
    LsaClose(policy);
    if (status != 0 || !data || !data->Buffer) return false;
    size_t chars = data->Length / sizeof(wchar_t);
    out.assign(data->Buffer, data->Buffer + chars);
    out.push_back(0);
    SecureZeroMemory(data->Buffer, data->Length);
    LsaFreeMemory(data);
    return chars > 0;
}

class Provider;
class Credential final : public ICredentialProviderCredential2 {
    volatile long refs_ = 1;
    Provider* owner_;
public:
    explicit Credential(Provider* owner);
    ~Credential();
    IFACEMETHODIMP QueryInterface(REFIID iid, void** out) override;
    IFACEMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&refs_); }
    IFACEMETHODIMP_(ULONG) Release() override {
        ULONG n = InterlockedDecrement(&refs_); if (!n) delete this; return n;
    }
    IFACEMETHODIMP Advise(ICredentialProviderCredentialEvents*) override { return S_OK; }
    IFACEMETHODIMP UnAdvise() override { return S_OK; }
    IFACEMETHODIMP SetSelected(BOOL* autoLogon) override;
    IFACEMETHODIMP SetDeselected() override { return S_OK; }
    IFACEMETHODIMP GetFieldState(DWORD id, CREDENTIAL_PROVIDER_FIELD_STATE* state,
                                CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE* interactive) override;
    IFACEMETHODIMP GetStringValue(DWORD id, PWSTR* value) override;
    IFACEMETHODIMP GetBitmapValue(DWORD id, HBITMAP* bitmap) override;
    IFACEMETHODIMP GetCheckboxValue(DWORD, BOOL*, PWSTR*) override { return E_NOTIMPL; }
    IFACEMETHODIMP GetSubmitButtonValue(DWORD, DWORD*) override { return E_NOTIMPL; }
    IFACEMETHODIMP GetComboBoxValueCount(DWORD, DWORD*, DWORD*) override { return E_NOTIMPL; }
    IFACEMETHODIMP GetComboBoxValueAt(DWORD, DWORD, PWSTR*) override { return E_NOTIMPL; }
    IFACEMETHODIMP SetStringValue(DWORD, PCWSTR) override { return E_NOTIMPL; }
    IFACEMETHODIMP SetCheckboxValue(DWORD, BOOL) override { return E_NOTIMPL; }
    IFACEMETHODIMP SetComboBoxSelectedValue(DWORD, DWORD) override { return E_NOTIMPL; }
    IFACEMETHODIMP CommandLinkClicked(DWORD) override { return E_NOTIMPL; }
    IFACEMETHODIMP GetSerialization(CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE*,
                                   CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION*, PWSTR*,
                                   CREDENTIAL_PROVIDER_STATUS_ICON*) override;
    IFACEMETHODIMP ReportResult(NTSTATUS, NTSTATUS, PWSTR*,
                               CREDENTIAL_PROVIDER_STATUS_ICON*) override;
    IFACEMETHODIMP GetUserSid(PWSTR* sid) override;
};

class Provider final : public ICredentialProvider, public ICredentialProviderSetUserArray {
    volatile long refs_ = 1;
    SRWLOCK lock_ = SRWLOCK_INIT;
    ICredentialProviderEvents* events_ = nullptr;
    UINT_PTR advise_context_ = 0;
    HANDLE thread_ = nullptr;
    DWORD thread_id_ = 0;
    HWND notify_window_ = nullptr;
    volatile LONG64 ready_until_ = 0;
    CREDENTIAL_PROVIDER_USAGE_SCENARIO scenario_ = CPUS_INVALID;
    bool user_visible_ = false;
    bool local_user_ = false;
    wchar_t qualified_user_[512]{};
    static DWORD WINAPI ThreadThunk(void* arg) { return static_cast<Provider*>(arg)->Listen(); }
    static LRESULT CALLBACK WindowProc(HWND window, UINT message, WPARAM wparam, LPARAM lparam);
    void NotifyChanged();
    DWORD Listen();
public:
    Provider() { InterlockedIncrement(&g_objects); }
    ~Provider();
    IFACEMETHODIMP QueryInterface(REFIID iid, void** out) override;
    IFACEMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&refs_); }
    IFACEMETHODIMP_(ULONG) Release() override {
        ULONG n = InterlockedDecrement(&refs_); if (!n) delete this; return n;
    }
    bool Ready() const { return GetTickCount64() < static_cast<ULONGLONG>(
        InterlockedCompareExchange64(const_cast<volatile LONG64*>(&ready_until_), 0, 0)); }
    bool ConsumeReady() {
        bool ready = Ready();
        InterlockedExchange64(&ready_until_, 0);
        return ready;
    }
    const wchar_t* QualifiedUser() const { return qualified_user_; }
    bool LocalUser() const { return local_user_; }
    CREDENTIAL_PROVIDER_USAGE_SCENARIO Scenario() const { return scenario_; }
    IFACEMETHODIMP SetUsageScenario(CREDENTIAL_PROVIDER_USAGE_SCENARIO scenario, DWORD) override {
        scenario_ = scenario;
        wchar_t sid[256]{};
        user_visible_ = ReadConfig(L"UserSid", sid, ARRAYSIZE(sid));
        Trace(scenario == CPUS_LOGON ? L"SetUsageScenario LOGON" :
              scenario == CPUS_UNLOCK_WORKSTATION ? L"SetUsageScenario UNLOCK" :
              L"SetUsageScenario OTHER");
        return scenario == CPUS_UNLOCK_WORKSTATION || scenario == CPUS_LOGON ? S_OK : E_NOTIMPL;
    }
    IFACEMETHODIMP SetSerialization(const CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION*) override {
        return E_NOTIMPL;
    }
    IFACEMETHODIMP Advise(ICredentialProviderEvents* events, UINT_PTR context) override;
    IFACEMETHODIMP UnAdvise() override;
    IFACEMETHODIMP GetFieldDescriptorCount(DWORD* count) override {
        if (!count) return E_INVALIDARG; *count = 3; return S_OK;
    }
    IFACEMETHODIMP GetFieldDescriptorAt(DWORD id, CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR** out) override {
        if (!out || id >= 3) return E_INVALIDARG;
        const CREDENTIAL_PROVIDER_FIELD_DESCRIPTOR fields[] = {
            {0, CPFT_TILE_IMAGE, const_cast<PWSTR>(L"FaceUnlock image"), CPFG_CREDENTIAL_PROVIDER_LOGO},
            {1, CPFT_SMALL_TEXT, const_cast<PWSTR>(L"FaceUnlock label"), CPFG_CREDENTIAL_PROVIDER_LABEL},
            {2, CPFT_LARGE_TEXT, const_cast<PWSTR>(L"FaceUnlock status"), {0}}
        };
        return FieldDescriptorCoAllocCopy(fields[id], out);
    }
    IFACEMETHODIMP GetCredentialCount(DWORD* count, DWORD* def, BOOL* autoLogon) override {
        if (!count || !def || !autoLogon) return E_INVALIDARG;
        bool ready = (scenario_ == CPUS_UNLOCK_WORKSTATION || scenario_ == CPUS_LOGON) && user_visible_ &&
                     InterlockedCompareExchange(&g_auth_failed, 0, 0) == 0 && Ready();
        // No tile while waiting, so the lock screen shows no sign of FaceUnlock.
        *count = ready ? 1 : 0;
        *def = ready ? 0 : CREDENTIAL_PROVIDER_NO_DEFAULT;
        *autoLogon = ready ? TRUE : FALSE;
        Trace(ready ? L"GetCredentialCount ready" :
              user_visible_ ? L"GetCredentialCount waiting" : L"GetCredentialCount no user");
        return S_OK;
    }
    IFACEMETHODIMP GetCredentialAt(DWORD id, ICredentialProviderCredential** out) override {
        if (!out || id || !user_visible_ || !Ready()) return E_INVALIDARG;
        auto* credential = new (std::nothrow) Credential(this);
        if (!credential) return E_OUTOFMEMORY;
        *out = credential; return S_OK;
    }
    IFACEMETHODIMP SetUserArray(ICredentialProviderUserArray* users) override {
        user_visible_ = false;
        local_user_ = false;
        qualified_user_[0] = 0;
        if (!users) return E_INVALIDARG;
        wchar_t target[256]{};
        if (!ReadConfig(L"UserSid", target, ARRAYSIZE(target))) return S_OK;
        DWORD count = 0;
        if (FAILED(users->GetCount(&count))) return S_OK;
        for (DWORD i = 0; i < count; ++i) {
            ICredentialProviderUser* user = nullptr;
            if (FAILED(users->GetAt(i, &user)) || !user) continue;
            PWSTR sid = nullptr;
            if (SUCCEEDED(user->GetSid(&sid)) && sid && _wcsicmp(target, sid) == 0) {
                const PROPERTYKEY key = INIT_PKEY_Identity_QualifiedUserName;
                PWSTR qualified = nullptr;
                GUID provider_id{};
                if (SUCCEEDED(user->GetStringValue(key, &qualified)) && qualified &&
                    SUCCEEDED(StringCchCopyW(qualified_user_, ARRAYSIZE(qualified_user_), qualified)) &&
                    SUCCEEDED(user->GetProviderID(&provider_id))) {
                    const GUID local = {0xA198529B, 0x730F, 0x4089,
                        {0xB6, 0x46, 0xA1, 0x25, 0x57, 0xF5, 0x66, 0x5E}};
                    local_user_ = provider_id == local;
                    user_visible_ = true;
                }
                CoTaskMemFree(qualified);
            }
            CoTaskMemFree(sid);
            user->Release();
        }
        Trace(user_visible_ ? L"SetUserArray matched" : L"SetUserArray no match");
        return S_OK;
    }
};

Credential::Credential(Provider* owner) : owner_(owner) {
    owner_->AddRef(); InterlockedIncrement(&g_objects);
}
Credential::~Credential() { owner_->Release(); InterlockedDecrement(&g_objects); }
HRESULT Credential::QueryInterface(REFIID iid, void** out) {
    if (!out) return E_POINTER;
    *out = nullptr;
    if (iid == IID_IUnknown || iid == IID_ICredentialProviderCredential ||
        iid == IID_ICredentialProviderCredential2) {
        *out = static_cast<ICredentialProviderCredential2*>(this); AddRef(); return S_OK;
    }
    return E_NOINTERFACE;
}
HRESULT Credential::SetSelected(BOOL* autoLogon) {
    if (!autoLogon) return E_INVALIDARG;
    *autoLogon = owner_->Ready() ? TRUE : FALSE;
    return S_OK;
}
HRESULT Credential::GetFieldState(DWORD id, CREDENTIAL_PROVIDER_FIELD_STATE* state,
                                  CREDENTIAL_PROVIDER_FIELD_INTERACTIVE_STATE* interactive) {
    if (id >= 3 || !state || !interactive) return E_INVALIDARG;
    *state = id == 0 ? CPFS_DISPLAY_IN_BOTH :
             id == 1 ? CPFS_HIDDEN : CPFS_DISPLAY_IN_SELECTED_TILE;
    *interactive = CPFIS_NONE;
    return S_OK;
}
HRESULT Credential::GetStringValue(DWORD id, PWSTR* value) {
    if (!value) return E_INVALIDARG;
    if (id == 1) return SHStrDupW(L"Oturum ac", value);
    if (id == 2) return SHStrDupW(L"", value);
    return E_INVALIDARG;
}
HRESULT Credential::GetBitmapValue(DWORD id, HBITMAP* bitmap) {
    if (id != 0 || !bitmap) return E_INVALIDARG;
    *bitmap = nullptr;
    BITMAPINFO info{};
    info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    info.bmiHeader.biWidth = 72;
    info.bmiHeader.biHeight = -72;
    info.bmiHeader.biPlanes = 1;
    info.bmiHeader.biBitCount = 32;
    info.bmiHeader.biCompression = BI_RGB;
    void* pixels = nullptr;
    HBITMAP image = CreateDIBSection(nullptr, &info, DIB_RGB_COLORS, &pixels, nullptr, 0);
    if (!image) return HRESULT_FROM_WIN32(GetLastError());
    HDC dc = CreateCompatibleDC(nullptr);
    if (!dc) { DeleteObject(image); return HRESULT_FROM_WIN32(GetLastError()); }
    HGDIOBJ old = SelectObject(dc, image);
    RECT bounds{0, 0, 72, 72};
    HBRUSH background = CreateSolidBrush(RGB(24, 83, 112));
    FillRect(dc, &bounds, background);
    DeleteObject(background);
    HBRUSH white = CreateSolidBrush(RGB(255, 255, 255));
    HGDIOBJ oldBrush = SelectObject(dc, white);
    HGDIOBJ oldPen = SelectObject(dc, GetStockObject(NULL_PEN));
    Ellipse(dc, 12, 10, 60, 58);
    SelectObject(dc, GetStockObject(DC_BRUSH));
    SetDCBrushColor(dc, RGB(24, 83, 112));
    Ellipse(dc, 17, 15, 55, 53);
    SelectObject(dc, white);
    Ellipse(dc, 25, 28, 30, 33);
    Ellipse(dc, 42, 28, 47, 33);
    RECT smile{27, 42, 45, 45};
    FillRect(dc, &smile, white);
    SelectObject(dc, oldPen);
    SelectObject(dc, oldBrush);
    DeleteObject(white);
    SelectObject(dc, old);
    DeleteDC(dc);
    *bitmap = image;
    return S_OK;
}
HRESULT Credential::GetUserSid(PWSTR* sid) {
    if (!sid) return E_INVALIDARG;
    *sid = nullptr;
    wchar_t configured[256]{};
    if (!ReadConfig(L"UserSid", configured, ARRAYSIZE(configured))) return E_FAIL;
    return SHStrDupW(configured, sid);
}
HRESULT Credential::GetSerialization(CREDENTIAL_PROVIDER_GET_SERIALIZATION_RESPONSE* response,
                                     CREDENTIAL_PROVIDER_CREDENTIAL_SERIALIZATION* serial,
                                     PWSTR* status, CREDENTIAL_PROVIDER_STATUS_ICON* icon) {
    if (!response || !serial || !status || !icon) return E_INVALIDARG;
    *response = CPGSR_NO_CREDENTIAL_NOT_FINISHED;
    *status = nullptr; *icon = CPSI_NONE;
    ZeroMemory(serial, sizeof(*serial));
    Trace(L"GetSerialization called");
    if (!owner_->ConsumeReady()) return E_ACCESSDENIED;
    const wchar_t* qualified = owner_->QualifiedUser();
    if (!qualified || !qualified[0]) return E_FAIL;
    PWSTR domain = nullptr, user = nullptr, protectedPassword = nullptr;
    std::vector<wchar_t> password;
    HRESULT hr = E_FAIL;
    if (!ReadPassword(password)) goto done;
    if (!owner_->LocalUser()) {
        // Microsoft's V2 sample uses identity-provider packing for MSA users.
        DWORD flags = CRED_PACK_PROTECTED_CREDENTIALS | CRED_PACK_ID_PROVIDER_CREDENTIALS;
        DWORD bytes = 0;
        CredPackAuthenticationBufferW(flags, const_cast<PWSTR>(qualified), password.data(), nullptr, &bytes);
        if (GetLastError() != ERROR_INSUFFICIENT_BUFFER || !bytes) goto done;
        serial->rgbSerialization = static_cast<BYTE*>(CoTaskMemAlloc(bytes));
        if (!serial->rgbSerialization) { hr = E_OUTOFMEMORY; goto done; }
        serial->cbSerialization = bytes;
        if (!CredPackAuthenticationBufferW(flags, const_cast<PWSTR>(qualified), password.data(),
                                           serial->rgbSerialization, &serial->cbSerialization)) {
            hr = HRESULT_FROM_WIN32(GetLastError());
            goto done;
        }
        hr = S_OK;
    } else {
        hr = SplitDomainAndUsername(qualified, &domain, &user);
        if (FAILED(hr)) goto done;
        hr = ProtectIfNecessaryAndCopyPassword(password.data(), owner_->Scenario(), &protectedPassword);
        if (FAILED(hr)) goto done;
        KERB_INTERACTIVE_UNLOCK_LOGON logon{};
        hr = KerbInteractiveUnlockLogonInit(domain, user, protectedPassword,
                                             owner_->Scenario(), &logon);
        if (SUCCEEDED(hr)) hr = KerbInteractiveUnlockLogonPack(logon, &serial->rgbSerialization,
                                                                &serial->cbSerialization);
    }
    if (SUCCEEDED(hr)) hr = RetrieveNegotiateAuthPackage(&serial->ulAuthenticationPackage);
    if (SUCCEEDED(hr)) {
        serial->clsidCredentialProvider = CLSID_FaceUnlock;
        *response = CPGSR_RETURN_CREDENTIAL_FINISHED;
        Trace(L"GetSerialization packed");
    }
done:
    if (FAILED(hr) && serial->rgbSerialization) {
        SecureZeroMemory(serial->rgbSerialization, serial->cbSerialization);
        CoTaskMemFree(serial->rgbSerialization);
        ZeroMemory(serial, sizeof(*serial));
    }
    if (protectedPassword) {
        SecureZeroMemory(protectedPassword, wcslen(protectedPassword) * sizeof(wchar_t));
        CoTaskMemFree(protectedPassword);
    }
    if (!password.empty()) SecureZeroMemory(password.data(), password.size() * sizeof(wchar_t));
    CoTaskMemFree(domain); CoTaskMemFree(user);
    return hr;
}
HRESULT Credential::ReportResult(NTSTATUS result, NTSTATUS, PWSTR* status,
                                 CREDENTIAL_PROVIDER_STATUS_ICON* icon) {
    if (result != STATUS_SUCCESS) InterlockedExchange(&g_auth_failed, 1);
    if (status) *status = nullptr;
    if (icon) *icon = CPSI_NONE;
    return S_OK;
}

Provider::~Provider() {
    UnAdvise();
    InterlockedDecrement(&g_objects);
}
HRESULT Provider::QueryInterface(REFIID iid, void** out) {
    if (!out) return E_POINTER;
    *out = nullptr;
    if (iid == IID_IUnknown || iid == IID_ICredentialProvider)
        *out = static_cast<ICredentialProvider*>(this);
    else if (iid == IID_ICredentialProviderSetUserArray)
        *out = static_cast<ICredentialProviderSetUserArray*>(this);
    else return E_NOINTERFACE;
    AddRef(); return S_OK;
}
HRESULT Provider::Advise(ICredentialProviderEvents* events, UINT_PTR context) {
    if (!events || (scenario_ != CPUS_UNLOCK_WORKSTATION && scenario_ != CPUS_LOGON)) return E_INVALIDARG;
    Trace(L"Advise called");
    AcquireSRWLockExclusive(&lock_);
    if (events_) events_->Release();
    events_ = events; events_->AddRef(); advise_context_ = context;
    ReleaseSRWLockExclusive(&lock_);
    if (!notify_window_) {
        WNDCLASSW wc{};
        wc.lpfnWndProc = WindowProc;
        wc.hInstance = g_instance;
        wc.lpszClassName = L"FaceUnlockProviderNotify";
        if (!RegisterClassW(&wc) && GetLastError() != ERROR_CLASS_ALREADY_EXISTS)
            return HRESULT_FROM_WIN32(GetLastError());
        notify_window_ = CreateWindowExW(0, wc.lpszClassName, L"", 0, 0, 0, 0, 0,
                                          HWND_MESSAGE, nullptr, g_instance, this);
        if (!notify_window_) return HRESULT_FROM_WIN32(GetLastError());
    }
    if (!thread_) {
        AddRef();
        thread_ = CreateThread(nullptr, 0, ThreadThunk, this, 0, &thread_id_);
        if (!thread_) { Release(); return HRESULT_FROM_WIN32(GetLastError()); }
    }
    return S_OK;
}
HRESULT Provider::UnAdvise() {
    Trace(L"UnAdvise called");
    AcquireSRWLockExclusive(&lock_);
    if (events_) { events_->Release(); events_ = nullptr; }
    HWND window = notify_window_;
    notify_window_ = nullptr;
    ReleaseSRWLockExclusive(&lock_);
    if (window) {
        if (GetWindowThreadProcessId(window, nullptr) == GetCurrentThreadId())
            DestroyWindow(window);
        else
            PostMessageW(window, WM_CLOSE, 0, 0);
    }
    if (thread_) {
        CancelSynchronousIo(thread_);
        if (GetCurrentThreadId() != thread_id_) WaitForSingleObject(thread_, 3000);
        CloseHandle(thread_); thread_ = nullptr;
    }
    InterlockedExchange64(&ready_until_, 0);
    return S_OK;
}
LRESULT CALLBACK Provider::WindowProc(HWND window, UINT message, WPARAM wparam, LPARAM lparam) {
    if (message == WM_NCCREATE) {
        auto* create = reinterpret_cast<CREATESTRUCTW*>(lparam);
        SetWindowLongPtrW(window, GWLP_USERDATA, reinterpret_cast<LONG_PTR>(create->lpCreateParams));
        return TRUE;
    }
    if (message == WM_FACE_UNLOCK || message == WM_TIMER) {
        if (message == WM_FACE_UNLOCK)
            SetTimer(window, READY_EXPIRED_TIMER, READY_MS + 100, nullptr);
        else
            KillTimer(window, READY_EXPIRED_TIMER);  // re-enumerate so an unused tile disappears
        auto* provider = reinterpret_cast<Provider*>(GetWindowLongPtrW(window, GWLP_USERDATA));
        if (provider) provider->NotifyChanged();
        return 0;
    }
    return DefWindowProcW(window, message, wparam, lparam);
}
void Provider::NotifyChanged() {
    Trace(L"CredentialsChanged notified");
    ICredentialProviderEvents* callback = nullptr;
    UINT_PTR context = 0;
    AcquireSRWLockShared(&lock_);
    if (events_) { callback = events_; callback->AddRef(); context = advise_context_; }
    ReleaseSRWLockShared(&lock_);
    if (callback) { callback->CredentialsChanged(context); callback->Release(); }
}
DWORD Provider::Listen() {
    wchar_t sid[256]{};
    if (!ReadConfig(L"UserSid", sid, ARRAYSIZE(sid))) { Release(); return 0; }
    wchar_t sddl[512]{};
    if (FAILED(StringCchPrintfW(sddl, ARRAYSIZE(sddl),
        L"D:P(A;;GA;;;SY)(A;;GA;;;%s)", sid))) { Release(); return 0; }
    PSECURITY_DESCRIPTOR descriptor = nullptr;
    if (!ConvertStringSecurityDescriptorToSecurityDescriptorW(
        sddl, SDDL_REVISION_1, &descriptor, nullptr)) { Release(); return 0; }
    SECURITY_ATTRIBUTES attributes{sizeof(attributes), descriptor, FALSE};
    while (true) {
        AcquireSRWLockShared(&lock_);
        bool active = events_ != nullptr;
        ReleaseSRWLockShared(&lock_);
        if (!active) break;
        HANDLE pipe = CreateNamedPipeW(PIPE_NAME,
            PIPE_ACCESS_INBOUND | FILE_FLAG_FIRST_PIPE_INSTANCE,
            PIPE_TYPE_MESSAGE | PIPE_READMODE_MESSAGE | PIPE_WAIT | PIPE_REJECT_REMOTE_CLIENTS,
            1, 32, 32, 0, &attributes);
        if (pipe == INVALID_HANDLE_VALUE) { Sleep(250); continue; }
        Trace(L"Pipe listening");
        bool connected = ConnectNamedPipe(pipe, nullptr) || GetLastError() == ERROR_PIPE_CONNECTED;
        char message[16]{};
        DWORD read = 0;
        ULONG client_session = static_cast<ULONG>(-1);
        ULONG server_session = static_cast<ULONG>(-1);
        ProcessIdToSessionId(GetCurrentProcessId(), &server_session);
        bool trusted = connected && IsTrustedClient(pipe);
        if (connected && !trusted) Trace(L"Pipe client rejected: not the installed agent");
        if (InterlockedCompareExchange(&g_auth_failed, 0, 0) == 0 &&
            trusted && GetNamedPipeClientSessionId(pipe, &client_session) &&
            client_session == server_session &&
            ReadFile(pipe, message, sizeof(message), &read, nullptr) &&
            read == 7 && memcmp(message, "UNLOCK\n", 7) == 0) {
            InterlockedExchange64(&ready_until_, static_cast<LONG64>(GetTickCount64() + READY_MS));
            Trace(L"Pipe signal accepted");
            HWND window = nullptr;
            AcquireSRWLockShared(&lock_);
            window = notify_window_;
            ReleaseSRWLockShared(&lock_);
            if (window) PostMessageW(window, WM_FACE_UNLOCK, 0, 0);
        }
        DisconnectNamedPipe(pipe);
        CloseHandle(pipe);
    }
    LocalFree(descriptor);
    Release();
    return 0;
}

class Factory final : public IClassFactory {
    volatile long refs_ = 1;
public:
    Factory() { InterlockedIncrement(&g_objects); }
    ~Factory() { InterlockedDecrement(&g_objects); }
    IFACEMETHODIMP QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (iid != IID_IUnknown && iid != IID_IClassFactory) return E_NOINTERFACE;
        *out = static_cast<IClassFactory*>(this); AddRef(); return S_OK;
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&refs_); }
    IFACEMETHODIMP_(ULONG) Release() override {
        ULONG n = InterlockedDecrement(&refs_); if (!n) delete this; return n;
    }
    IFACEMETHODIMP CreateInstance(IUnknown* outer, REFIID iid, void** out) override {
        if (outer) return CLASS_E_NOAGGREGATION;
        auto* provider = new (std::nothrow) Provider();
        if (!provider) return E_OUTOFMEMORY;
        HRESULT hr = provider->QueryInterface(iid, out);
        provider->Release(); return hr;
    }
    IFACEMETHODIMP LockServer(BOOL lock) override {
        InterlockedExchangeAdd(&g_objects, lock ? 1 : -1); return S_OK;
    }
};

STDAPI DllGetClassObject(REFCLSID clsid, REFIID iid, void** out) {
    if (clsid != CLSID_FaceUnlock) return CLASS_E_CLASSNOTAVAILABLE;
    auto* factory = new (std::nothrow) Factory();
    if (!factory) return E_OUTOFMEMORY;
    HRESULT hr = factory->QueryInterface(iid, out);
    factory->Release(); return hr;
}
STDAPI DllCanUnloadNow() {
    return g_objects == 0 ? S_OK : S_FALSE;
}
BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, void*) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_instance = instance;
        DisableThreadLibraryCalls(instance);
    }
    return TRUE;
}
