// src/ffi.rs — C ABI wrappers for Python/FFI integration
use std::ffi::{CStr, CString};
use std::os::raw::c_char;

#[no_mangle]
pub extern "C" fn hinsdale_analyze(hex_ptr: *const c_char) -> *mut c_char {
    if hex_ptr.is_null() { return std::ptr::null_mut(); }
    let hex = unsafe { CStr::from_ptr(hex_ptr) }.to_string_lossy();
    let result = match crate::parse_hex(&hex) {
        Ok(bytes) => {
            let report = crate::analyze(&bytes);
            serde_json::to_string(&report).unwrap_or_else(|e| {
                serde_json::json!({ "error": e.to_string() }).to_string()
            })
        }
        Err(e) => serde_json::json!({ "error": e }).to_string(),
    };
    CString::new(result).map(|s| s.into_raw()).unwrap_or(std::ptr::null_mut())
}

#[no_mangle]
pub extern "C" fn hinsdale_free(ptr: *mut c_char) {
    if !ptr.is_null() {
        unsafe { drop(CString::from_raw(ptr)); }
    }
}

#[no_mangle]
pub extern "C" fn hinsdale_version() -> *const c_char {
    b"hinsdale 2.0.0\0".as_ptr() as *const c_char
}
