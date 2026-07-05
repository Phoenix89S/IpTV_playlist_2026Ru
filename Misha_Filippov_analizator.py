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

    # TXT файл со списком
    with open("icons_list.txt", "w", encoding="utf-8") as txt:
        for icon in icons:
            txt.write(f"{folder_path}/{icon}\n")

    # HTML файл
    with open(os.path.join(folder_path, "head.html"), "w", encoding="utf-8") as html:
        html.write("<!DOCTYPE html>\n<html lang='ru'>\n<head>\n<meta charset='UTF-8'>\n<title>Логотипы</title>\n</head>\n<body>\n<ul>\n")
        for icon in icons:
            html.write(f"    <li><img src='{icon}' alt='{os.path.splitext(icon)[0]}'></li>\n")
        html.write("</ul>\n</body>\n</html>")

    # XML файл
    with open(os.path.join(folder_path, "head.xml"), "w", encoding="utf-8") as xml:
        xml.write("<?xml version='1.0' encoding='UTF-8'?>\n<icons>\n")
        for icon in icons:
            xml.write(f"    <icon path='{folder_path}/{icon}' name='{os.path.splitext(icon)[0]}' />\n")
        xml.write("</icons>")

    print("✅ Готово! Созданы: icons_list.txt, icons/head.html и icons/head.xml.")


if __name__ == "__main__":
    Misha_Filippov_analizator()