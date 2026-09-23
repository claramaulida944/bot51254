"""
stealth_bot/remake.py
Engine Generator Remake Cerita (Reader Remixes) untuk simulasi intervensi pembaca alami.
Mendukung 7 Mode Intervensi Resmi:
1. status_window_next_chapter (Jendela Status RPG / Level Up)
2. attractive_existing_character (Penampilan Baru Tokoh yang Ada)
3. self_insert_next_chapter (Intervensi Tokoh Baru / Pembaca)
4. attractive_self_insert (Tokoh Baru Memikat / Daya Tarik Spesial)
5. mutual_fate_next_chapter (Kartu Takdir Bersama Dua Karakter)
6. constellation_next_chapter (Rasi Bintang / Sponsor Transenden)
7. character_regression (Regresi Waktu / Membawa Memori Masa Depan)
"""

import random
from typing import Dict, Any, Optional, List


class RemakeModeGenerator:
    """Generator opsi, atribut, dan prompt intervensi Remake Cerita dengan variasi tinggi."""

    AVAILABLE_MODES = [
        "status_window_next_chapter",
        "attractive_existing_character",
        "self_insert_next_chapter",
        "attractive_self_insert",
        "mutual_fate_next_chapter",
        "constellation_next_chapter",
        "character_regression",
    ]

    CHARACTERS_POOL = [
        "Julian", "April", "Clara", "Ramy", "Harris", "Elena", "Denis",
        "Letisa", "Arga", "Reyhan", "Karin", "Maya", "Dion", "Edrick",
        "Mira", "Thomas", "Sugi", "Zahra", "Aditya", "Nadine", "Lian",
        "Alana", "Damian", "Devan", "Bima", "Tara", "Revan", "Viona"
    ]

    EXP_RULES = [
        "Saat berhasil mengelabui lawan dalam konfrontasi",
        "Ketika selamat dari situasi bahaya kritis",
        "Saat mengungkap rahasia tersembunyi kubu musuh",
        "Setiap kali berhasil melindungi orang lain dari maut",
        "Saat membuat keputusan strategis yang mengubah alur cerita",
        "Ketika berlatih keras dalam kesendirian hingga fajar",
        "Saat berhasil menegosiasikan kesepakatan berisiko tinggi",
        "Ketika menahan rasa sakit fisik demi tujuan mulia",
        "Saat menemukan petunjuk konspirasi keluarga tersembunyi",
        "Setiap kali menolak tunduk pada tekanan kekuasaan tiran",
        "Ketika membongkar kebohongan orang terdekat",
        "Saat berhasil mengumpulkan bukti dokumen rahasia",
    ]

    GROWTH_STATS_POOL = [
        "Wawasan", "Pesona", "Keberuntungan", "Kekuatan", "Ketangkasan",
        "Kecerdasan", "Daya Tahan", "Otoritas", "Mana", "Insting",
        "Karisma", "Pengaruh", "Konsentrasi", "Refleks", "Ketabahan",
        "Aura Pembunuh", "Intuisi Taktis", "Ketajaman Mental"
    ]

    STATUS_TITLES = [
        "Status Pertumbuhan", "Jendela Status Kebangkitan", "Sistem Transenden Protagonis",
        "Panel Kemampuan Tersembunyi", "Status Warisan Leluhur", "Jendela Takdir Baru",
        "Sistem Penakluk Bayangan", "Panel Otoritas Mutlak"
    ]

    APPEARANCES_POOL = [
        "Mengenakan mantel wol hitam panjang dengan tatapan dingin penuh karisma",
        "Gaya rambut baru yang lebih rapi dengan setelan jas formal berkelas tinggi",
        "Berpakaian serba hitam dengan aura misterius dan bekas luka tipis di alis",
        "Mengenakan seragam militer taktis dengan postur tegap dan senyum tipis memikat",
        "Gaun sutra malam elegan dengan liontin perak kuno yang memancarkan cahaya redup",
        "Tampil sederhana dengan kemeja putih kasual namun memiliki tatapan tajam tak terbantahkan",
        "Mengenakan jubah bangsawan berkerah bulu dengan pedang berukir di pinggang",
        "Rambut terurai bebas dengan mata perak yang tampak berkilat di bawah temaram lampu",
        "Setelan jas abu-abu arang dengan jam saku perak antik warisan keluarga",
        "Pakaian kasual bertudung hitam yang menyamarkan ekspresi wajah di tengah keramaian"
    ]

    ROLES_POOL = [
        "Detektif swasta independen yang disewa oleh informan rahasia",
        "Dokter ahli bedah lapangan yang terjebak di tengah perselisihan keluarga konglomerat",
        "Pengawal pribadi baru yang diam-diam memiliki agenda penyelidikan pribadi",
        "Pialang saham cerdik yang mengetahui skandal finansial tersembunyi",
        "Arsitek muda pemegang cetak biru rahasia bangunan persembunyian",
        "Mantan agen intelijen yang sedang mencari tempat suaka aman",
        "Kolektor barang antik penyimpan artefak perjanjian masa lalu",
        "Diplomat netral yang datang membawa peringatan bahaya dari luar negeri",
        "Penyusup bayaran yang berniat membatalkan kontrak demi membantu tokoh utama",
        "Asisten pribadi berdarah dingin yang sebenarnya adalah pewaris yang disembunyikan"
    ]

    BACKGROUNDS_POOL = [
        "Memiliki koneksi rahasia dengan sindikat bawah tanah dan tetap tenang di bawah todongan senjata.",
        "Hanya bertindak atas dasar bukti objektif, namun memiliki kode moral ketat untuk tidak menyakiti orang tak bersalah.",
        "Mengetahui skenario besar musuh dan berusaha mengubah arah takdir sebelum titik kehancuran.",
        "Tampak ramah dan bersahaja dari luar, namun memiliki insting tempur dan deduksi luar biasa tajam.",
        "Memiliki masa lalu yang kelam dengan keluarga bangsawan utama dan ingin menuntut keadilan sejati."
    ]

    CHARMS_POOL = [
        "Memiliki daya tarik magnetis yang sulit diabaikan serta wibawa tenang yang mendominasi seisi ruangan.",
        "Tutur kata yang santun namun penuh perhitungan strategis, memikat sekaligus membuat lawan bicara waspada.",
        "Tatapan mata tajam yang sanggup membaca ketakutan tersembunyi, diimbangi senyum percaya diri yang menawan.",
        "Aura misterius yang membangkitkan rasa ingin tahu sang protagonis untuk terus mendekat."
    ]

    FATE_RELATIONS = [
        "Keduanya terikat sumpah darah leluhur di mana rasa sakit satu sama lain saling beresonansi secara fisik.",
        "Keduanya memiliki sepasang liontin kembar antik yang mulai berpendar saat salah satu menghadapi bahaya maut.",
        "Takdir masa lalu yang bertentangan kini memaksa mereka berbagi satu-satunya kunci keselamatan.",
        "Sebuah ramalan kuno menyatakan bahwa keberhasilan yang satu bergantung mutlak pada kesetiaan yang lain.",
        "Garis keturunan mereka saling terkait dalam rahasia kematian misterius generasi terdahulu."
    ]

    CONSTELLATIONS = [
        "Hakim Malam Abadi", "Pengamat Ujung Horison", "Naga Perak dari Utara",
        "Ratu Kegelapan Tanpa Mahkota", "Penjaga Rahasia Samudra", "Arsitek Bintang Pertama",
        "Pemberontak Langit Kelabu", "Penakluk Waktu yang Hilang"
    ]

    CONSTELLATION_MESSAGES = [
        "'Tunjukkan padaku apakah tekadmu sanggup melampaui harga yang harus dibayar.'",
        "'Aku telah mengawasimu sejak hari pertama. Terimalah berkah ini jika nyalimu belum surut.'",
        "'Jalan di depanmu dipenuhi jebakan duri pengkhianatan, namun sorot mataku takkan berpaling.'",
        "'Pilihanmu di detik ini akan menentukan apakah namamu tercatat abadi di antara bintang.'",
        "'Keberanian sejati bukan ketiadaan rasa takut, melainkan langkah maju di tengah kegelapan.'"
    ]

    REGRESSION_TURNING_POINTS = [
        "Kembali ke momen 24 jam sebelum dokumen perjanjian kepemilikan ditandatangani secara paksa.",
        "Membawa seluruh ingatan kegagalan di masa depan untuk mencegah kematian tragis orang terkasih.",
        "Mengetahui siapa dalang pengkhianat di lingkaran terdekat sebelum racun sempat dituangkan.",
        "Menyadari bahwa musuh terbesar selama ini bukanlah sosok yang tampak di garis depan.",
        "Mengulangi malam lelang sita aset dengan persiapan finansial dan kartu as tersembunyi."
    ]

    @classmethod
    def generate(
        cls,
        mode: Optional[str] = None,
        target_character: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Menghasilkan konfigurasi remake, parameter PUT, dan prompt streaming acak dengan variasi tinggi."""
        chosen_mode = mode if mode in cls.AVAILABLE_MODES else random.choice(cls.AVAILABLE_MODES)
        char = target_character if target_character else random.choice(cls.CHARACTERS_POOL)

        if chosen_mode == "status_window_next_chapter":
            rule = random.choice(cls.EXP_RULES)
            stats = random.sample(cls.GROWTH_STATS_POOL, 3)
            title = random.choice(cls.STATUS_TITLES)
            msg = (
                f"[INTERVENSI JENDELA STATUS]\n"
                f"Tokoh target: {char}\n"
                f"Syarat EXP: {rule}\n"
                f"Statistik pertumbuhan: {', '.join(stats)}\n"
                f"Tulis satu bab berikutnya yang utuh, tempat aturan ini benar-benar mengubah kejadian dan pilihan."
            )
            return {
                "mode": chosen_mode,
                "title": "Jendela Status RPG (Level Up)",
                "summary": f"Tokoh: {char} | Syarat: {rule[:35]}... | Stats: {', '.join(stats)}",
                "stream_message": msg,
                "put_endpoint": "status-window",
                "put_payload": {
                    "target_character": char,
                    "experience_rule": rule,
                    "growth_stats": stats,
                    "status_title": title,
                    "profile_visible": True,
                },
            }

        elif chosen_mode == "attractive_existing_character":
            app = random.choice(cls.APPEARANCES_POOL)
            msg = (
                f"Tokoh yang akan diubah: {char}\n"
                f"Penampilan yang diinginkan: {app}\n"
                f"Buat bab berikutnya sekarang dengan penampilan baru tokoh ini."
            )
            return {
                "mode": chosen_mode,
                "title": "Ubah Penampilan Tokoh",
                "summary": f"Tokoh: {char} | Penampilan: {app[:40]}...",
                "stream_message": msg,
                "put_endpoint": "existing-character-attractive",
                "put_payload": {
                    "target_character": char,
                    "appearance": app,
                    "profile_visible": False,
                },
            }

        elif chosen_mode == "self_insert_next_chapter":
            role = random.choice(cls.ROLES_POOL)
            bg = random.choice(cls.BACKGROUNDS_POOL)
            msg = (
                f"[INTERVENSI TOKOH BARU]\n"
                f"Peran: {role}\n"
                f"Latar Belakang: {bg}\n"
                f"Masukkan tokoh ini ke dalam jalan cerita bab berikutnya sebagai figur kunci yang mempengaruhi keputusan tokoh utama."
            )
            return {
                "mode": chosen_mode,
                "title": "Intervensi Tokoh Baru",
                "summary": f"Peran: {role[:40]}... | Sifat: {bg[:35]}...",
                "stream_message": msg,
                "put_endpoint": None,
                "put_payload": None,
            }

        elif chosen_mode == "attractive_self_insert":
            role = random.choice(cls.ROLES_POOL)
            charm = random.choice(cls.CHARMS_POOL)
            msg = (
                f"[INTERVENSI TOKOH MEMIKAT]\n"
                f"Peran: {role}\n"
                f"Daya Tarik: {charm}\n"
                f"Bawa tokoh ini hadir di bab berikutnya dengan dinamika ketegangan dan ketertarikan yang intens bersama tokoh utama."
            )
            return {
                "mode": chosen_mode,
                "title": "Tokoh Baru Memikat",
                "summary": f"Peran: {role[:40]}... | Karisma: {charm[:35]}...",
                "stream_message": msg,
                "put_endpoint": None,
                "put_payload": None,
            }

        elif chosen_mode == "mutual_fate_next_chapter":
            fate = random.choice(cls.FATE_RELATIONS)
            msg = (
                f"[INTERVENSI TAKDIR BERSAMA]\n"
                f"Ikatan Takdir: {fate}\n"
                f"Tulis bab berikutnya di mana benang merah takdir ini mulai terungkap dan mengubah hubungan kedua karakter."
            )
            return {
                "mode": chosen_mode,
                "title": "Kartu Takdir Bersama",
                "summary": f"Takdir: {fate[:50]}...",
                "stream_message": msg,
                "put_endpoint": None,
                "put_payload": None,
            }

        elif chosen_mode == "constellation_next_chapter":
            const = random.choice(cls.CONSTELLATIONS)
            c_msg = random.choice(cls.CONSTELLATION_MESSAGES)
            msg = (
                f"[INTERVENSI RASI BINTANG]\n"
                f"Rasi Bintang Transenden: {const}\n"
                f"Pesan Pertama: {c_msg}\n"
                f"Hadirkan intervensi entitas rasi bintang ini dalam bab berikutnya untuk memberikan berkah atau ujian misterius."
            )
            return {
                "mode": chosen_mode,
                "title": "Rasi Bintang / Sponsor",
                "summary": f"Entitas: {const} | Pesan: {c_msg[:40]}...",
                "stream_message": msg,
                "put_endpoint": None,
                "put_payload": None,
            }

        else:  # character_regression
            tp = random.choice(cls.REGRESSION_TURNING_POINTS)
            msg = (
                f"[INTERVENSI REGRESI WAKTU]\n"
                f"Tokoh yang Regresi: {char}\n"
                f"Titik Balik: {tp}\n"
                f"Tulis bab berikutnya di mana tokoh ini bertindak dengan pengetahuan masa depan, membalikkan keadaan secara dramatis."
            )
            return {
                "mode": "character_regression",
                "title": "Regresi Waktu (Time Travel)",
                "summary": f"Tokoh: {char} | Titik Balik: {tp[:45]}...",
                "stream_message": msg,
                "put_endpoint": None,
                "put_payload": None,
            }
