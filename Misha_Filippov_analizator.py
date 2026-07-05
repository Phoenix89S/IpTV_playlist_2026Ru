import os

def Misha_Filippov_analizator(folder_path="icons"):
    # Проверяем наличие папки
    if not os.path.isdir(folder_path):
        print(f"Папка {folder_path} не найдена.")
        return

    # Допустимые расширения
    valid_ext = {".png", ".jpg", ".jpeg", ".svg"}
    icons = []

    # Проходим по всем файлам в папке
    for file in os.listdir(folder_path):
        ext = os.path.splitext(file)[1].lower()
        if ext in valid_ext:
            icons.append(file)

    # TXT файл со списком (в корне)
    with open("icons_list.txt", "w", encoding="utf-8") as txt:
        for icon in icons:
            txt.write(f"{folder_path}/{icon}\n")

    # HTML файл (в корне)
    with open("head.html", "w", encoding="utf-8") as html:
        html.write("<!DOCTYPE html>\n<html lang='ru'>\n<head>\n<meta charset='UTF-8'>\n<title>Логотипы</title>\n</head>\n<body>\n<ul>\n")
        for icon in icons:
            html.write(f"    <li><img src='{folder_path}/{icon}' alt='{os.path.splitext(icon)[0]}'></li>\n")
        html.write("</ul>\n</body>\n</html>")

    # XML файл (в корне)
    with open("head.xml", "w", encoding="utf-8") as xml:
        xml.write("<?xml version='1.0' encoding='UTF-8'?>\n<icons>\n")
        for icon in icons:
            xml.write(f"    <icon path='{folder_path}/{icon}' name='{os.path.splitext(icon)[0]}' />\n")
        xml.write("</icons>")

    print("✅ Готово! Итоговые файлы сохранены в корне: icons_list.txt, head.html и head.xml.")


if __name__ == "__main__":
    Misha_Filippov_analizator()