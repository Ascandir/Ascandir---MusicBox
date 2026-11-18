"""MusicBox: Klang-Nachbearbeitung wie im ACE-Step-Original.

1. MusicBoxAceDCWSampler – Euler-Sampler mit DCW („Differential Correction in Wavelet domain“, CVPR 2026).
   ACE-Step schaltet DCW bei den Turbo-Modellen standardmäßig ein (acestep/models/common/dcw_correction.py:
   mode="double", scaler=0.05, high_scaler=0.02, wavelet="haar", J=1, mode="zero").
   Nach jedem Schritt: x ← x + t·0.05·Tief(x − x0) + (1−t)·0.02·Hoch(x − x0), x0 = vorhergesagtes sauberes Signal.
2. MusicBoxPeakNormalize – Spitzen auf −1 dB setzen (ACE-Step: normalize_audio(..., -1.0)), damit beim Speichern
   nichts übersteuert und abgeschnitten wird.

Wird von MusicBox automatisch nach ComfyUI/custom_nodes kopiert.
"""
import torch
import torch.nn.functional as F


def _haar_low(d):
    """Tiefpass-Anteil einer 1-stufigen Haar-Zerlegung entlang der Zeit (letzte Achse), zurück im Zeitbereich.
    Entspricht IDWT(DWT(d).low, 0) mit pytorch_wavelets (haar, J=1, mode='zero')."""
    T = d.shape[-1]
    if T % 2:
        d = F.pad(d, (0, 1))
    m = (d[..., 0::2] + d[..., 1::2]) * 0.5
    low = torch.stack((m, m), dim=-1).flatten(-2)
    return low[..., :T]


def dcw_double(x, y, low_s, high_s):
    if low_s == 0.0 and high_s == 0.0:
        return x
    dtype = x.dtype
    d = (x - y).float()
    low = _haar_low(d)
    high = d - low
    return (x.float() + low_s * low + high_s * high).to(dtype)


@torch.no_grad()
def sample_euler_dcw(model, x, sigmas, extra_args=None, callback=None, disable=None, low_scaler=0.05,
                     high_scaler=0.02):
    extra_args = {} if extra_args is None else extra_args
    s_in = x.new_ones([x.shape[0]])
    for i in range(len(sigmas) - 1):
        sigma = sigmas[i]
        denoised = model(x, sigma * s_in, **extra_args)
        if callback is not None:
            callback({"x": x, "i": i, "sigma": sigma, "sigma_hat": sigma, "denoised": denoised})
        if float(sigmas[i + 1]) == 0.0:
            x_next = denoised                                    # letzter Schritt: direkt das saubere Signal
        else:
            d = (x - denoised) / sigma
            x_next = x + d * (sigmas[i + 1] - sigma)
        t = float(sigma)
        x = dcw_double(x_next, denoised, t * low_scaler, (1.0 - t) * high_scaler)
    return x


class MusicBoxAceDCWSampler:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "low_scaler": ("FLOAT", {"default": 0.05, "min": 0.0, "max": 1.0, "step": 0.005}),
            "high_scaler": ("FLOAT", {"default": 0.02, "min": 0.0, "max": 1.0, "step": 0.005}),
        }}

    RETURN_TYPES = ("SAMPLER",)
    FUNCTION = "get"
    CATEGORY = "musicbox"

    def get(self, low_scaler, high_scaler):
        import comfy.samplers
        return (comfy.samplers.KSAMPLER(sample_euler_dcw, extra_options={"low_scaler": low_scaler,
                                                                          "high_scaler": high_scaler}),)


class MusicBoxPeakNormalize:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "audio": ("AUDIO",),
            "target_db": ("FLOAT", {"default": -1.0, "min": -30.0, "max": 0.0, "step": 0.1}),
        }}

    RETURN_TYPES = ("AUDIO",)
    FUNCTION = "normalize"
    CATEGORY = "musicbox"

    def normalize(self, audio, target_db):
        w = audio["waveform"]
        peak = w.abs().amax(dim=[-1, -2], keepdim=True).clamp(min=1e-8)
        target = 10.0 ** (target_db / 20.0)
        return ({**audio, "waveform": w * (target / peak)},)


NODE_CLASS_MAPPINGS = {"MusicBoxAceDCWSampler": MusicBoxAceDCWSampler, "MusicBoxPeakNormalize": MusicBoxPeakNormalize}
NODE_DISPLAY_NAME_MAPPINGS = {"MusicBoxAceDCWSampler": "MusicBox ACE-Step Sampler (DCW)",
                              "MusicBoxPeakNormalize": "MusicBox Spitzen auf −1 dB"}
