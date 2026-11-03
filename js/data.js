/* =========================================================
   M&D Beauty Salon — дані сайту
   Редагуйте цей файл, щоб змінити послуги, ціни, майстрів
   та фотографії в галереї. Розмітку змінювати не потрібно.
   ========================================================= */

window.SALON = {
  /* Налаштування онлайн-запису.
     endpoint — URL вашого сервера / Google Apps Script / Formspree тощо,
     куди буде надіслано заявку методом POST (JSON).
     Якщо порожньо — заявка зберігається лише в браузері клієнта
     і показується підтвердження з пропозицією зателефонувати. */
  booking: {
    endpoint: "",
    openHour: 9,          // початок роботи (будні)
    closeHour: 20,        // кінець роботи (будні)
    weekendOpen: 10,      // неділя
    weekendClose: 18,
    slotMinutes: 30,
    daysAhead: 30
  },

  contacts: {
    phone: "+38 (067) 123-45-67",
    phoneRaw: "+380671234567",
    address: "м. Київ, вул. Садова, 12",
    instagram: "https://instagram.com/",
    telegram: "https://t.me/",
    mapQuery: "Київ, вулиця Садова 12"
  },

  categories: [
    {
      id: "hair",
      name: "Волосся",
      icon: "✂",
      image: "images/hair-styling.jpg",
      description: "Стрижки, складне фарбування, відновлення та укладки від стилістів-колористів.",
      services: [
        { name: "Жіноча стрижка", duration: 60, price: 650, from: true },
        { name: "Чоловіча стрижка", duration: 45, price: 450 },
        { name: "Укладка (брашинг, локони)", duration: 45, price: 500, from: true },
        { name: "Фарбування в один тон", duration: 120, price: 1500, from: true },
        { name: "Складне фарбування (AirTouch, балаяж)", duration: 240, price: 3200, from: true },
        { name: "Кератинове випрямлення", duration: 180, price: 2500, from: true },
        { name: "Ботокс для волосся", duration: 120, price: 1800, from: true },
        { name: "Вечірня або весільна зачіска", duration: 90, price: 1200, from: true }
      ]
    },
    {
      id: "nails",
      name: "Нігті",
      icon: "◆",
      image: "images/nails-design.jpg",
      description: "Манікюр і педикюр зі стерильними інструментами та стійким покриттям.",
      services: [
        { name: "Класичний манікюр", duration: 45, price: 350 },
        { name: "Манікюр + покриття гель-лак", duration: 90, price: 650 },
        { name: "Зміцнення нігтів гелем", duration: 105, price: 800 },
        { name: "Нарощування нігтів", duration: 150, price: 1100, from: true },
        { name: "Дизайн (1 ніготь)", duration: 0, price: 30, from: true },
        { name: "Smart-педикюр", duration: 60, price: 700 },
        { name: "Педикюр + покриття гель-лак", duration: 90, price: 850 }
      ]
    },
    {
      id: "brows",
      name: "Брови та вії",
      icon: "❖",
      image: "images/makeup.jpg",
      description: "Архітектура брів, ламінування та нарощування вій з натуральним ефектом.",
      services: [
        { name: "Корекція брів", duration: 30, price: 250 },
        { name: "Корекція + фарбування брів", duration: 45, price: 450 },
        { name: "Ламінування брів", duration: 60, price: 650 },
        { name: "Ламінування вій", duration: 60, price: 750 },
        { name: "Нарощування вій (класика)", duration: 120, price: 800 },
        { name: "Нарощування вій (2D–3D)", duration: 150, price: 1000 }
      ]
    },
    {
      id: "makeup",
      name: "Макіяж",
      icon: "✦",
      image: "images/makeup-brushes.jpg",
      description: "Денний, вечірній та весільний макіяж на професійній косметиці.",
      services: [
        { name: "Денний макіяж", duration: 60, price: 800 },
        { name: "Вечірній макіяж", duration: 75, price: 1100 },
        { name: "Весільний макіяж", duration: 90, price: 1800 },
        { name: "Весільний макіяж + пробний", duration: 180, price: 2800 },
        { name: "Урок макіяжу для себе", duration: 120, price: 1500 }
      ]
    },
    {
      id: "face",
      name: "Догляд за обличчям",
      icon: "❀",
      image: "images/face-care.jpg",
      description: "Чистки, пілінги та доглядові програми під ваш тип шкіри.",
      services: [
        { name: "Консультація косметолога", duration: 30, price: 0 },
        { name: "Комбінована чистка обличчя", duration: 90, price: 1200 },
        { name: "Хімічний пілінг", duration: 60, price: 900, from: true },
        { name: "Зволожувальна доглядова програма", duration: 60, price: 1000 },
        { name: "Альгінатна маска", duration: 30, price: 450 },
        { name: "Масаж обличчя", duration: 45, price: 700 }
      ]
    },
    {
      id: "spa",
      name: "SPA та масаж",
      icon: "❦",
      image: "images/spa-massage.jpg",
      description: "Розслаблюючі та оздоровчі масажі, стоун-терапія та SPA-ритуали.",
      services: [
        { name: "Масаж спини та шиї", duration: 30, price: 600 },
        { name: "Загальний класичний масаж", duration: 60, price: 1200 },
        { name: "Лімфодренажний масаж", duration: 60, price: 1100 },
        { name: "Стоун-терапія", duration: 75, price: 1400 },
        { name: "SPA-ритуал «M&D Signature»", duration: 120, price: 2400 }
      ]
    }
  ],

  masters: [
    { name: "Марія", role: "Засновниця, стиліст-колорист", categories: ["hair", "makeup"], exp: "12 років досвіду" },
    { name: "Діана", role: "Засновниця, топ-майстер нігтьового сервісу", categories: ["nails"], exp: "10 років досвіду" },
    { name: "Олена", role: "Бровіст, лешмейкер", categories: ["brows", "makeup"], exp: "7 років досвіду" },
    { name: "Ірина", role: "Косметолог, масажист", categories: ["face", "spa"], exp: "9 років досвіду" }
  ],

  gallery: [
    { src: "images/hair-color.jpg", cat: "hair", title: "Фарбування AirTouch" },
    { src: "images/nails-design.jpg", cat: "nails", title: "Дизайн «Чорний мармур»", tall: true },
    { src: "images/makeup.jpg", cat: "makeup", title: "Вечірній макіяж" },
    { src: "images/face-mask.jpg", cat: "face", title: "Доглядова маска" },
    { src: "images/hair-styling.jpg", cat: "hair", title: "Брашинг та укладка" },
    { src: "images/nails-gel.jpg", cat: "nails", title: "Нюдовий гель-лак з блиском" },
    { src: "images/hair-care.jpg", cat: "hair", title: "Відновлювальний догляд", tall: true },
    { src: "images/spa-massage.jpg", cat: "face", title: "Стоун-терапія" },
    { src: "images/nails-manicure.jpg", cat: "nails", title: "Класичний манікюр" },
    { src: "images/hair-curls.jpg", cat: "hair", title: "Голлівудські локони" },
    { src: "images/face-care.jpg", cat: "face", title: "Зволожувальна програма" },
    { src: "images/makeup-brushes.jpg", cat: "makeup", title: "Робоче місце візажиста" }
  ]
};
